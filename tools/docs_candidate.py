"""Extract the documentation bytes of a verified, successful binary candidate."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import zipfile

from docs_site import check_site
from promote import verify_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--source-sha', required=True)
    args = parser.parse_args()
    run = json.loads(subprocess.check_output(['gh', 'api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{args.run_id}']))
    if (run['conclusion'] != 'success' or run['status'] != 'completed' or run['head_sha'] != args.source_sha
            or run['path'] != '.github/workflows/candidate.yml' or run['event'] != 'workflow_dispatch'
            or run['head_repository']['full_name'] != os.environ['GITHUB_REPOSITORY']):
        raise ValueError('Expected a successful Build candidate run at the reviewed source.')
    directory = Path('dist/docs-candidate')
    manifest = verify_candidate(directory, commit=args.source_sha, run_id=args.run_id,
                                run_attempt=run['run_attempt'], platform='linux-x64', allow_earlier_attempt=True)
    destination = Path('dist/docs-site')
    if destination.exists():
        raise ValueError('Documentation extraction destination must be new.')
    with zipfile.ZipFile(directory / f'portfolio-breakdown-{manifest["version"]}-docs.zip') as archive:
        for item in archive.infolist():
            path = Path(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe documentation archive member.')
        archive.extractall(destination)
    info = check_site(destination)
    if info['source_sha'] != args.source_sha:
        raise ValueError('Documentation source differs from the binary candidate.')


if __name__ == '__main__':
    main()
