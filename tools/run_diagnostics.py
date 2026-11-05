"""Run selected checks without creating release acceptance evidence."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def selections(requested, canonical):
    if not requested or len(requested) != len(set(requested)):
        raise ValueError('Select distinct collected tests or test files')
    selected = []
    for value in requested:
        if not value.startswith('tests/') or '..' in value.split('/') or '\\' in value:
            raise ValueError('Only collected repository tests may be selected')
        matches = [n for n in canonical if n == value or n.split('::', 1)[0] == value]
        if not matches:
            raise ValueError(f'Unknown or empty test selection: {value}')
        selected.extend(matches)
    if len(selected) != len(set(selected)):
        raise ValueError('Overlapping test selections')
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodeid', action='append')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--cpu-rate', type=int, choices=[1, 4], default=1)
    parser.add_argument('--suite', choices=['tests', 'source-smoke', 'native-startup', 'native-interaction', 'packaged-browser', 'installer', 'installer-upgrade'], default='tests')
    parser.add_argument('--directory', type=Path, default=ROOT/'dist/candidate')
    parser.add_argument('--artifact-run')
    parser.add_argument('--artifact-name')
    parser.add_argument('--build-artifact', action='store_true', help='Build once before packaged diagnostics when no saved bytes exist')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.directory = args.directory.resolve()
    if not 1 <= args.repeat <= 10:
        parser.error('Repetitions must be between 1 and 10')
    packaged = args.suite not in ('tests', 'source-smoke')
    if args.build_artifact and (not packaged or args.artifact_run):
        parser.error('Build only for packaged checks without a saved artifact run')
    if bool(args.artifact_run) != bool(args.artifact_name):
        parser.error('Supply both artifact run and name')
    args.output.mkdir(parents=True, exist_ok=False)
    from release_evidence import identity
    report = dict(identity(), kind='diagnostic', publishable=False, suite=args.suite, attempts=[])
    try:
        env = dict(os.environ, PORTFOLIO_REQUIRE_BROWSER='1', PORTFOLIO_TEST_CPU_RATE=str(args.cpu_rate))
        if args.suite == 'tests':
            with TemporaryDirectory(prefix='portfolio-diagnostic-collection-') as tmp:
                subprocess.run([sys.executable, str(ROOT/'tools/run_candidate_tests.py'), '--worker', 'canonical',
                                '--output', tmp], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
                canonical = json.loads((Path(tmp)/'canonical.collection.json').read_text())['nodeids']
            selected = selections(args.nodeid or os.environ.get('TEST_NODEIDS', '').splitlines(), canonical)
            report['nodeids'] = selected
        if packaged and args.build_artifact:
            args.directory = args.output/'candidate'
            with (args.output/'build.log').open('w', encoding='utf-8') as log:
                subprocess.run([sys.executable, str(ROOT/'tools/release.py'), 'build', '--directory', str(args.directory)],
                               cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        if packaged and args.artifact_run:
            from promote import GitHub, safe_extract, verify_candidate
            from release_evidence import checked_run
            repository = os.environ['GITHUB_REPOSITORY']
            api = GitHub(repository, os.environ['GH_TOKEN'])
            checked_run(api, repository, args.artifact_run, report['source'], {'.github/workflows/candidate.yml'})
            listing = api.call(f'/actions/runs/{args.artifact_run}/artifacts?per_page=100')
            artifacts = listing['artifacts']
            if listing.get('total_count', len(artifacts)) != len(artifacts):
                raise ValueError('Incomplete diagnostic artifact listing')
            matches = [a for a in artifacts if a['name'] == args.artifact_name and not a['expired']]
            if len(matches) != 1:
                raise ValueError('Missing or ambiguous diagnostic artifact')
            archive = args.output/'artifact.zip'
            api.download(matches[0]['id'], archive)
            args.directory = args.output/'candidate'
            safe_extract(archive, args.directory)
            manifest = json.loads((args.directory/'manifest.json').read_text())
            verify_candidate(args.directory, commit=report['source'], run_id=str(args.artifact_run),
                             run_attempt=manifest['run_attempt'], platform=manifest['platform'], require_publishable=False)
            report['artifact'] = dict(id=matches[0]['id'], manifest=manifest)
        if packaged:
            from promote import verify_candidate
            manifest = json.loads((args.directory/'manifest.json').read_text())
            target = 'windows-x64' if os.name == 'nt' else 'linux-x64'
            verify_candidate(args.directory, commit=report['source'], run_id=manifest['run_id'],
                             run_attempt=manifest['run_attempt'], platform=target, require_publishable=False)
            if manifest['lock_sha256'] != report['lock_sha256']:
                raise ValueError('Diagnostic artifact dependency lock differs from checkout')
        for attempt in range(1, args.repeat+1):
            evidence = args.output/str(attempt)
            evidence.mkdir()
            if args.suite == 'tests':
                command = [sys.executable, '-m', 'pytest', '-x', '-v', '--durations=10',
                           '--junitxml='+str(evidence/'tests.xml'), *selected]
            elif args.suite == 'source-smoke':
                command = [sys.executable, str(ROOT/'tools/package_smoke.py'), '--evidence', str(evidence)]
            else:
                command = [sys.executable, str(ROOT/'tools/release.py'), 'test', '--directory', str(args.directory),
                           '--stage', args.suite, '--evidence', str(evidence)]
            started = time.monotonic()
            with (evidence/'check.log').open('w', encoding='utf-8') as log:
                result = subprocess.run(command, cwd=ROOT, env=dict(env, PORTFOLIO_BROWSER_EVIDENCE=str(evidence/'browser')),
                                        stdout=log, stderr=subprocess.STDOUT)
            report['attempts'].append(dict(number=attempt, command=command, exit_code=result.returncode,
                                           seconds=time.monotonic()-started, evidence=str(evidence)))
            print(json.dumps(report['attempts'][-1]), flush=True)
            if result.returncode:
                return result.returncode
        return 0
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        (args.output/'diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
