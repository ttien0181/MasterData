"""Copy only hash-approved E5 assets into the existing stack's read-only model mount."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'artifacts/advanced-m2a-20261014/e5-trial-01'
    target = ROOT / 'data/embedding/e5-cpu-linux/model'
    manifest = json.loads((source / 'model-manifest.json').read_text(encoding='utf8'))
    assert manifest['revision'] == '614241f622f53c4eeff9890bdc4f31cfecc418b3'
    for item in manifest['files']:
        original = (source / 'model' / item['path']).resolve()
        destination = (target / item['path']).resolve()
        assert original.is_relative_to((source / 'model').resolve())
        assert destination.is_relative_to(target.resolve())
        assert hashlib.sha256(original.read_bytes()).hexdigest() == item['sha256']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            assert hashlib.sha256(destination.read_bytes()).hexdigest() == item['sha256']
        else:
            shutil.copyfile(original, destination)
    build = ROOT / 'backend/encoder'
    (build / 'wheels').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / 'model-manifest.json', build / 'model-manifest.json')
    wheel = ROOT / 'artifacts/four-ai-20261015/encoder-download/torch-2.14.1+cpu-cp311-cp311-manylinux_2_28_x86_64.whl'
    if wheel.exists():
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == '5e38154c8896d426a5df58bf2276b603e82184dfae4f54d77dbb212f36e34f37'
        shutil.copyfile(wheel, build / 'wheels' / wheel.name)
    print(json.dumps({'model_files_verified': len(manifest['files']), 'model_mount': str(target)}))


if __name__ == '__main__':
    main()
