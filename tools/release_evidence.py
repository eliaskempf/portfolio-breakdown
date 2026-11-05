"""Bind reusable source validation and acceptance to immutable source/artifacts."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import stat
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLATFORMS = ('linux-x64', 'windows-x64')


def identity(checkout=ROOT):
    return dict(source=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip(),
                source_clean=not subprocess.check_output(['git', 'status', '--porcelain'], cwd=checkout, text=True).strip(),
                lock_sha256=sha256((checkout / 'uv.lock').read_bytes()).hexdigest(),
                python=platform.python_version(), system=platform.system(),
                run_id=os.environ.get('GITHUB_RUN_ID', 'local'),
                run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT', '1'))


def check_source(directory, *, source, lock, target):
    """Recompute coverage from raw collection/execution/JUnit, not a green label."""
    from run_candidate_tests import final_evidence
    manifest = json.loads((directory / 'manifest.json').read_text())
    result = json.loads((directory / 'results.json').read_text())
    canonical = json.loads((directory / 'canonical.collection.json').read_text())
    expected_system = 'Windows' if target == 'windows-x64' else 'Linux'
    if (target not in SOURCE_PLATFORMS or result.get('source') != source
            or manifest.get('source') != source or result.get('lock_sha256') != lock
            or result.get('system') != expected_system or not result.get('python', '').startswith('3.12.')
            or result.get('status') != 'passed' or canonical.get('skipped_browsers') != []
            or result.get('source_unchanged') is not True):
        raise ValueError('Source validation identity/environment does not match')
    if set(manifest['groups']) != {'unit-0', 'unit-1', 'responsiveness', 'browser-0', 'browser-1'}:
        raise ValueError('Incomplete source-validation groups')
    with TemporaryDirectory(prefix='portfolio-evidence-check-') as temporary:
        verified = final_evidence(directory, manifest['groups'], result['exit_codes'], canonical['nodeids'],
                                  source_unchanged=True, aggregate=Path(temporary) / 'verified.xml')
    if verified['status'] != 'passed' or not canonical['nodeids']:
        raise ValueError('Source validation coverage is incomplete or failed')
    return result


def checked_run(api, repository, run_id, source, paths):
    if not str(run_id).isdecimal() or int(run_id) < 1:
        raise ValueError('Evidence requires a numeric workflow run ID')
    run = api.call(f'/actions/runs/{run_id}')
    if (run.get('status') != 'completed' or run.get('head_sha') != source
            or run.get('head_repository', {}).get('full_name') != repository or run.get('path') not in paths):
        raise ValueError('Evidence run does not match source/repository/workflow')
    return run


def check_jobs(api, run_id, names, source):
    response = api.call(f'/actions/runs/{run_id}/jobs?filter=latest&per_page=100')
    jobs = response['jobs']
    if response['total_count'] != len(jobs):
        raise ValueError('Incomplete workflow job evidence')
    selected = []
    for name in names:
        matches = [j for j in jobs if j['name'].split(' / ')[-1] == name]
        if (len(matches) != 1 or matches[0].get('status') != 'completed'
                or matches[0].get('conclusion') != 'success' or matches[0].get('head_sha') != source):
            raise ValueError(f'Required evidence job did not pass: {name}')
        selected.append(matches[0])
    return selected


def extract_evidence(archive, destination):
    destination.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if (len(names) != len(set(names)) or any('/' in n or '\\' in n or n in ('', '.', '..') for n in names)
                or any(stat.S_ISLNK(i.external_attr >> 16) for i in zipped.infolist())):
            raise ValueError('Unsafe evidence archive')
        if sum(i.file_size for i in zipped.infolist()) > 40_000_000:
            raise ValueError('Evidence archive too large')
        zipped.extractall(destination)


def source_run(api, repository, run_id, source, lock):
    run = checked_run(api, repository, run_id, source, {
        '.github/workflows/ci.yml', '.github/workflows/candidate.yml', '.github/workflows/source-validation.yml'})
    jobs = check_jobs(api, run_id, ['source-' + p for p in SOURCE_PLATFORMS], source)
    response = api.call(f'/actions/runs/{run_id}/artifacts?per_page=100')
    if response.get('total_count', len(response['artifacts'])) != len(response['artifacts']):
        raise ValueError('Incomplete source artifact listing')
    with TemporaryDirectory(prefix='portfolio-source-evidence-') as tmp:
        for target, job in zip(SOURCE_PLATFORMS, jobs):
            matches = [a for a in response['artifacts'] if a['name'] == 'source-evidence-' + target and not a['expired']]
            if len(matches) != 1:
                raise ValueError('Missing or ambiguous source evidence')
            archive, directory = Path(tmp) / (target+'.zip'), Path(tmp) / target
            api.download(matches[0]['id'], archive)
            extract_evidence(archive, directory)
            result = check_source(directory, source=source, lock=lock, target=target)
            attempt = result.get('run_attempt')
            if result.get('run_id') != str(run_id) or not str(attempt).isdecimal():
                raise ValueError('Source evidence run identity mismatch')
            from promote import retained_build
            if not (1 <= int(attempt) <= int(run['run_attempt'])) or (
                    int(attempt) != job.get('run_attempt') and not retained_build(api, run_id, job, int(attempt))):
                raise ValueError('Stale source validation attempt')
    return dict(source=source, lock_sha256=lock, source_run=str(run_id))


def acceptance(directory, stage, operation, output):
    """Write success only after the selected check and unchanged artifact digest."""
    before = sha256((directory / 'manifest.json').read_bytes()).hexdigest()
    manifest = json.loads((directory / 'manifest.json').read_text())
    current = identity()
    if output.exists():
        raise ValueError('Acceptance output already exists; use a fresh evidence directory')
    if not current['source_clean'] or manifest['commit'] != current['source'] or manifest['lock_sha256'] != current['lock_sha256']:
        raise ValueError('Acceptance checkout differs from artifact source/lock')
    operation()
    after = identity()
    if any(after[key] != current[key] for key in ('source', 'lock_sha256', 'source_clean')):
        raise ValueError('Acceptance checkout changed during validation')
    from promote import verify_candidate
    verify_candidate(directory, commit=current['source'], run_id=manifest['run_id'],
                     run_attempt=manifest['run_attempt'], platform=manifest['platform'], require_publishable=False)
    if sha256((directory / 'manifest.json').read_bytes()).hexdigest() != before:
        raise ValueError('Acceptance changed the artifact manifest')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(current, stage=stage, status='passed', manifest_sha256=before,
                                     build_run=manifest['run_id'], build_attempt=manifest['run_attempt']), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['source', 'accept', 'record'])
    parser.add_argument('--run-id')
    parser.add_argument('--directory', type=Path, default=ROOT/'dist/candidate')
    parser.add_argument('--stage', default='all', choices=['native', 'all', 'packaged-browser', 'native-startup', 'native-interaction', 'installer', 'installer-upgrade'])
    parser.add_argument('--evidence', type=Path, default=ROOT/'dist/acceptance')
    args = parser.parse_args()
    if args.action == 'source':
        from promote import GitHub
        info = identity()
        if not info['source_clean']:
            raise ValueError('Source reuse requires a clean frozen checkout')
        result = source_run(GitHub(os.environ['GITHUB_REPOSITORY'], os.environ['GH_TOKEN']),
                            os.environ['GITHUB_REPOSITORY'], args.run_id, info['source'], info['lock_sha256'])
        print(json.dumps(result))
    else:
        if (args.action == 'record') != (args.stage == 'native'):
            parser.error('record is reserved for native workflow checks; accept requires an executable stage')
        from release import test_candidate
        acceptance(args.directory, args.stage,
                   (lambda: None) if args.action == 'record' else (lambda: test_candidate(args.directory, stage=args.stage, evidence=args.evidence)),
                   args.evidence/'acceptance.json')


if __name__ == '__main__':
    main()
