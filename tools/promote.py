"""Promote an explicitly accepted Actions candidate without rebuilding binaries."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


def verify_candidate(directory, *, commit, run_id, run_attempt, platform, require_publishable=True,
                     allow_earlier_attempt=False):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    expected = dict(commit=commit, run_id=str(run_id), platform=platform)
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError('Candidate identity does not match the successful workflow run.')
    attempt = manifest.get('run_attempt', '')
    # Re-run failed jobs retains successful platforms from an earlier attempt of
    # this same run/SHA. Their artifacts are uploaded only after package tests.
    if (not isinstance(attempt, str) or not re.fullmatch('[1-9][0-9]*', attempt)
            or not (1 <= int(attempt) <= int(run_attempt) if allow_earlier_attempt else attempt == str(run_attempt))):
        raise ValueError('Candidate attempt identity does not match the successful workflow run.')
    if manifest.get('schema') not in {1, 2} or (require_publishable and manifest['schema'] != 2):
        raise ValueError('A current installer/documentation candidate is required for publication.')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[abrc]+\d+)?', manifest.get('version', '')):
        raise ValueError('Invalid candidate version.')
    if not re.fullmatch(r'[0-9a-f]{64}', manifest.get('lock_sha256', '')):
        raise ValueError('Missing dependency lock identity.')
    if require_publishable and manifest.get('icon_ready') is not True:
        raise ValueError('Required application/favicon artwork is missing; candidates are testable but not publishable.')
    if require_publishable and manifest.get('source_clean') is not True:
        raise ValueError('Only candidates from a clean checkout can be published.')
    files = manifest.get('files', {})
    version = manifest['version']
    if platform not in {'windows-x64', 'linux-x64', 'linux-native-x64', 'macos-arm64'}:
        raise ValueError('Unknown candidate platform.')
    bundle = f'portfolio-breakdown-{version}-{platform}' + ('.zip' if platform == 'windows-x64' else '.tar.gz')
    if platform == 'linux-native-x64':
        bundle = f'portfolio-breakdown-experimental_{version}_amd64.deb'
    if platform == 'macos-arm64':
        bundle = f'portfolio-breakdown-experimental-{version}-macos-arm64.dmg'
        if (manifest.get('experimental') is not True or manifest.get('developer_id_signed') is not False
                or manifest.get('notarized') is not False or manifest.get('manual_installation_verified') is not False):
            raise ValueError('Experimental macOS limitations must be explicit.')
    required = {bundle, f'portfolio_breakdown-{version}-py3-none-any.whl',
                f'portfolio_breakdown-{version}.tar.gz', 'THIRD_PARTY_NOTICES.txt', 'dependencies.json'}
    if manifest['schema'] == 2:
        required.add(f'portfolio-breakdown-{version}-docs.zip')
        docs = manifest.get('documentation', {})
        if (docs.get('source_sha') != commit or docs.get('route') != f'candidates/{commit}/'
                or not re.fullmatch('[a-f0-9]{64}', docs.get('build_info_sha256', ''))):
            raise ValueError('Documentation does not match candidate source.')
        if platform == 'windows-x64':
            required.add(f'portfolio-breakdown-{version}-windows-x64-setup.exe')
    if set(files) != required:
        raise ValueError('Candidate contains missing or unexpected release files.')
    sums = {}
    for line in (directory / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        checksum, filename = line.split('  ', 1)
        if filename in sums:
            raise ValueError('Duplicate checksum entry.')
        sums[filename] = checksum
    if set(sums) != required | {'manifest.json'}:
        raise ValueError('Incomplete checksum list.')
    if set(p.name for p in directory.iterdir()) != required | {'manifest.json', 'SHA256SUMS'}:
        raise ValueError('Unexpected candidate content.')
    for name in required | {'manifest.json'}:
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe artifact member.')
        actual = sha256(path.read_bytes()).hexdigest()
        if sums[name] != actual or (name != 'manifest.json' and files[name] != actual):
            raise ValueError(f'Checksum mismatch: {name}')
    if manifest['schema'] == 2:
        with zipfile.ZipFile(directory / f'portfolio-breakdown-{version}-docs.zip') as archive:
            info_bytes = archive.read('build-info.json')
            info = json.loads(info_bytes)
            if (sha256(info_bytes).hexdigest() != docs['build_info_sha256']
                    or info.get('source_sha') != commit or info.get('app_version') != version
                    or info.get('dirty') is not False or info.get('channel') != 'candidate'):
                raise ValueError('Documentation archive does not match candidate identity.')
            members = [item.filename for item in archive.infolist() if not item.is_dir()]
            if len(members) != len(set(members)) or set(members) != set(info['files']) | {'build-info.json'}:
                raise ValueError('Unexpected documentation archive content.')
            for name, checksum in info['files'].items():
                path = Path(name)
                if (path.is_absolute() or '..' in path.parts or '\\' in name
                        or sha256(archive.read(name)).hexdigest() != checksum):
                    raise ValueError('Unsafe or modified documentation content.')
    return manifest


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid repository name.')
        self.base = f'https://api.github.com/repos/{repository}'
        self.token = token

    def call(self, path, *, method='GET', body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, method=method,
                          headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                                   'Content-Type': 'application/json'})
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    def download(self, artifact, destination):
        request = Request(self.base + f'/actions/artifacts/{artifact}/zip',
                          headers={'Authorization': 'Bearer ' + self.token})
        # GitHub redirects artifact downloads to signed storage URLs. urllib does
        # not need an application credential on those redirects.
        import urllib.request
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
                if redirected is not None:
                    redirected.remove_header('Authorization')
                return redirected
        with urllib.request.build_opener(Redirect).open(request, timeout=120) as response:
            destination.write_bytes(response.read())

    def upload(self, release_id, path):
        url = self.base.replace('api.github.com', 'uploads.github.com') + f'/releases/{release_id}/assets?name={quote(path.name)}'
        request = Request(url, data=path.read_bytes(), method='POST',
                          headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/octet-stream'})
        with urlopen(request, timeout=300) as response:
            return json.load(response)


def safe_extract(archive, destination):
    destination.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate artifact paths.')
        for member in zipped.infolist():
            if member.is_dir() or Path(member.filename).name != member.filename or '\\' in member.filename:
                raise ValueError('Unexpected artifact layout.')
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlink in artifact.')
        zipped.extractall(destination)


def desktop_run(api, repository, run_id, commit, branch, platform):
    """Require each installer's build and compatibility evidence independently."""
    run = api.call(f'/actions/runs/{run_id}')
    if (run['status'] != 'completed' or run['event'] != 'workflow_dispatch'
            or run['path'] != '.github/workflows/desktop-experiment.yml'
            or run['head_branch'] != branch or run['head_sha'] != commit
            or run['head_repository']['full_name'] != repository):
        raise ValueError(f'{platform} must come from the same release commit in a completed desktop workflow.')
    response = api.call(f'/actions/runs/{run_id}/jobs?filter=latest&per_page=100')
    jobs = response['jobs']
    required = ({'build-linux-x64', 'linux-compatibility-x11', 'linux-compatibility-wayland'}
                if platform == 'linux-native-x64' else
                {'build-macos-arm64', 'macos-compatibility-macos-15', 'macos-compatibility-macos-26'})
    if response['total_count'] != len(jobs):
        raise ValueError(f'Incomplete {platform} workflow job evidence.')
    for name in required:
        matches = [job for job in jobs if job['name'] == name]
        if (len(matches) != 1 or matches[0]['status'] != 'completed'
                or matches[0]['conclusion'] != 'success' or matches[0].get('head_sha') != commit):
            raise ValueError(f'Required {platform} job did not pass: {name}')
    return run, [job for job in jobs if job['name'] in required]


def promote(api, repository, run_id, *, linux_native_run_id, macos_run_id=None):
    if not str(linux_native_run_id).isdecimal():
        raise ValueError('A numeric native Linux candidate run ID is required.')
    repo = api.call('')
    run = api.call(f'/actions/runs/{run_id}')
    if (run['conclusion'] != 'success' or run['status'] != 'completed' or run['event'] != 'workflow_dispatch'
            or run['path'] != '.github/workflows/candidate.yml' or run['head_branch'] != repo['default_branch']
            or run['head_repository']['full_name'] != repository):
        raise ValueError('Only successful manually built candidates from this repository/default branch can be published.')
    commit = run['head_sha']
    artifacts = api.call(f'/actions/runs/{run_id}/artifacts?per_page=100')['artifacts']
    with TemporaryDirectory(prefix='portfolio-publish-') as temporary:
        root = Path(temporary)
        manifests = []
        uploads = []
        for platform in ['linux-x64', 'windows-x64']:
            matches = [a for a in artifacts if a['name'] == 'candidate-' + platform and not a['expired']]
            if len(matches) != 1:
                raise ValueError('Missing, expired or ambiguous candidate artifact.')
            archive = root / f'{platform}.zip'
            api.download(matches[0]['id'], archive)
            folder = root / platform
            safe_extract(archive, folder)
            manifest = verify_candidate(folder, commit=commit, run_id=run_id,
                                        run_attempt=run['run_attempt'], platform=platform, allow_earlier_attempt=True)
            manifests.append(manifest)
            for name in manifest['files']:
                path = folder / name
                if name in {'THIRD_PARTY_NOTICES.txt', 'dependencies.json'}:
                    path.rename(folder / f'{platform}-{name}')
                    uploads.append(folder / f'{platform}-{name}')
                elif platform == 'linux-x64' or name.endswith(('-windows-x64.zip', '-windows-x64-setup.exe')):
                    uploads.append(path)
            path = folder / 'manifest.json'
            path.rename(folder / f'{platform}-manifest.json')
            uploads.append(folder / f'{platform}-manifest.json')
        desktop_candidates = [('linux-native-x64', linux_native_run_id, 'build-linux-x64', '.deb')]
        if macos_run_id:
            desktop_candidates.append(('macos-arm64', macos_run_id, 'build-macos-arm64', '.dmg'))
        for platform, desktop_run_id, build_job, suffix in desktop_candidates:
            selected_run, jobs = desktop_run(api, repository, desktop_run_id, commit, repo['default_branch'], platform)
            artifacts = api.call(f'/actions/runs/{desktop_run_id}/artifacts?per_page=100')['artifacts']
            matches = [a for a in artifacts if a['name'] == 'candidate-' + platform and not a['expired']]
            if len(matches) != 1:
                raise ValueError(f'Missing, expired or ambiguous {platform} candidate artifact.')
            archive = root / f'{platform}.zip'
            api.download(matches[0]['id'], archive)
            folder = root / platform
            safe_extract(archive, folder)
            manifest = verify_candidate(folder, commit=commit, run_id=desktop_run_id,
                run_attempt=selected_run['run_attempt'], platform=platform, allow_earlier_attempt=True)
            # Compatibility must cover the retained installer, not an older build.
            attempt = int(manifest['run_attempt'])
            for job in jobs:
                checked_attempt = job.get('run_attempt')
                if (type(checked_attempt) is not int or not attempt <= checked_attempt <= selected_run['run_attempt']
                        or job['name'] == build_job and checked_attempt != attempt):
                    raise ValueError(f'{platform} job evidence predates or does not identify the uploaded build.')
            manifests.append(manifest)
            for name in manifest['files']:
                path = folder / name
                if name.endswith(suffix):
                    uploads.append(path)
                elif name in {'THIRD_PARTY_NOTICES.txt', 'dependencies.json'} or name.startswith('portfolio_breakdown-'):
                    path.rename(folder / f'{platform}-{name}')
                    uploads.append(folder / f'{platform}-{name}')
            path = folder / 'manifest.json'
            path.rename(folder / f'{platform}-manifest.json')
            uploads.append(folder / f'{platform}-manifest.json')
        if len({m['version'] for m in manifests}) != 1 or len({m['lock_sha256'] for m in manifests}) != 1:
            raise ValueError('Platforms were built from different versions or dependency locks.')
        if len({m['documentation']['build_info_sha256'] for m in manifests}) != 1:
            raise ValueError('Platforms have different documentation contents.')
        tag = 'v' + manifests[0]['version']
        sums = root / 'SHA256SUMS'
        sums.write_text(''.join(f'{sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in uploads), encoding='utf-8')
        uploads.append(sums)
        # Do not mutate tags or releases until every artifact has been validated.
        try:
            existing = api.call(f'/git/ref/tags/{tag}')
        except HTTPError as exc:
            if exc.code != 404:
                raise
            existing = None
        if existing and existing['object']['sha'] != commit:
            raise ValueError('Release tag already points elsewhere.')
        if not existing:
            api.call('/git/refs', method='POST', body={'ref': 'refs/tags/' + tag, 'sha': commit})
        mac_note = (f'\n\nExperimental macOS installer (Apple Silicon, macOS 14+): '
            f'https://github.com/{repository}/actions/runs/{macos_run_id}\n'
            'Automated packaged-app checks passed on macOS 14, 15 and 26. '
            'No Developer ID signing or notarization; Gatekeeper may block normal launch. '
            'Interactive installation/download approval on a personal Mac remains unverified.' if macos_run_id else '')
        linux_note = (f'\n\nUbuntu 22.04/24.04 x64 native installer: '
            f'https://github.com/{repository}/actions/runs/{linux_native_run_id}\n'
            'The .deb retains the portfolio-breakdown-experimental package and application-menu name. '
            'The Linux browser archive remains available.')
        try:
            release = api.call('/releases/tags/' + tag)
        except HTTPError as exc:
            if exc.code != 404:
                raise
            release = api.call('/releases', method='POST', body=dict(tag_name=tag, target_commitish=commit,
                name=tag, draft=True, body=f'Tested candidate: https://github.com/{repository}/actions/runs/{run_id}\n\n'
                    'GPL-3.0-only. Corresponding source, build instructions and dependency notices are attached.' + linux_note + mac_note))
        if not release['draft']:
            raise ValueError('An official release already exists; it will not be overwritten.')
        if release.get('assets'):
            raise ValueError('Draft already contains assets. Inspect the interrupted publication before retrying.')
        for path in uploads:
            api.upload(release['id'], path)
        publication = {'draft': False}
        body = release.get('body', '')
        for note in [linux_note, mac_note]:
            if note and note not in body:
                body += note
        if body != release.get('body', ''):
            publication['body'] = body
        api.call(f'/releases/{release["id"]}', method='PATCH', body=publication)
        print(f'Published {tag} from candidate run {run_id}; no artifacts were rebuilt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--linux-native-run-id', required=True)
    parser.add_argument('--acceptance', required=True, choices=['tested-on-windows-and-linux'])
    parser.add_argument('--macos-run-id', default='')
    parser.add_argument('--experimental-macos', action='store_true', help='Accept the disclosed experimental Mac limitations')
    args = parser.parse_args()
    if not args.run_id.isdecimal() or not args.linux_native_run_id.isdecimal():
        parser.error('Run IDs must be numeric.')
    if (bool(args.macos_run_id) != args.experimental_macos
            or args.macos_run_id and not args.macos_run_id.isdecimal()):
        parser.error('Experimental macOS needs a numeric run ID and --experimental-macos together.')
    repository = os.environ['GITHUB_REPOSITORY']
    promote(GitHub(repository, os.environ['GH_TOKEN']), repository, args.run_id,
            linux_native_run_id=args.linux_native_run_id, macos_run_id=args.macos_run_id)


if __name__ == '__main__':
    main()
