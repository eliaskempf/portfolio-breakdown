"""Assess a disposable quarantined copy without changing Gatekeeper policy."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


def run(app: Path, output: Path):
    if sys.platform != 'darwin':
        raise RuntimeError('Gatekeeper requires a macOS host')
    output.mkdir(parents=True, exist_ok=False)
    copy = output / 'Quarantined Synthetic Test.app'
    subprocess.run(['ditto', str(app), str(copy)], check=True)
    report = {'status': 'failed', 'expected': 'unsigned distribution rejected',
              'gaps': ['interactive Open Anyway approval', 'actual browser download quarantine propagation']}
    def command(name, args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        report[name] = {'returncode': result.returncode, 'output': result.stdout + result.stderr}
        return result
    try:
        integrity = command('integrity', ['codesign', '--verify', '--deep', '--strict', str(copy)])
        assert integrity.returncode == 0, 'Invalid bundle signature, distinct from Developer ID trust'
        status = command('policy', ['spctl', '--status'])
        assert 'assessments enabled' in status.stdout, 'Runner is not enforcing Gatekeeper'
        quarantine = f'0083;{int(time.time()):x};PortfolioSyntheticTest;{uuid.uuid4()}'
        subprocess.run(['xattr', '-w', 'com.apple.quarantine', quarantine, str(copy)], check=True)
        command('quarantine', ['xattr', '-p', 'com.apple.quarantine', str(copy)])
        assessment = command('assessment', ['spctl', '--assess', '--type', 'execute', '-vv', str(copy)])
        assert assessment.returncode != 0 and 'rejected' in assessment.stderr.lower(), 'Expected explicit Gatekeeper rejection'
        if shutil.which('syspolicy_check'):
            command('distribution', ['syspolicy_check', 'distribution', str(copy)])
        marker = output / 'unexpected-native-launch'
        with (output / 'launch.log').open('w') as log:
            launch = subprocess.Popen(['open', '-W', '-n', str(copy), '--args',
                '--native-self-test', str(marker), 'early-close'], stdout=log, stderr=log)
            try:
                launch.wait(timeout=20)
                report['launch_returncode'] = launch.returncode
            except subprocess.TimeoutExpired:
                launch.terminate()
                launch.wait(timeout=5)
                report['launch_returncode'] = 'approval dialog / launch wait timed out'
        assert not marker.exists(), 'Quarantined app unexpectedly entered its self-test'
        report['status'] = 'passed'
        report['observed'] = 'integrity valid; Gatekeeper rejects; quarantined launch did not enter application'
    finally:
        (output / 'gatekeeper-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        # Preserve evidence, not another large app bundle in the report artifact.
        shutil.rmtree(copy)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.app.resolve(), args.output.resolve())
