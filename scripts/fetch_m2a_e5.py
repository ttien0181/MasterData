"""Download only the approved E5 revision; verify upstream hashes before use."""
import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
MODEL = 'intfloat/multilingual-e5-small'
FILES = ('README.md', 'config.json', 'model.safetensors', 'modules.json',
         'sentence_bert_config.json', 'sentencepiece.bpe.model', 'special_tokens_map.json',
         'tokenizer.json', 'tokenizer_config.json', '1_Pooling/config.json')


def main():
    out = ROOT / 'artifacts/advanced-m2a-20261014/e5-trial-01'
    folder = out / 'model'
    folder.mkdir(parents=True, exist_ok=True)
    response = requests.get(f'https://huggingface.co/api/models/{MODEL}/revision/{REVISION}',
                            params={'blobs': 'true'}, timeout=30)
    response.raise_for_status()
    metadata = response.json()
    if metadata['sha'] != REVISION:
        raise ValueError('MODEL_REVISION_MISMATCH')
    entries = {entry['rfilename']: entry for entry in metadata['siblings']}
    pins = []
    for name in FILES:
        entry = entries[name]
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.partial')
        with requests.get(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}',
                          stream=True, timeout=(30, 120)) as download:
            download.raise_for_status()
            digest, size = hashlib.sha256(), 0
            with partial.open('wb') as handle:
                for block in download.iter_content(1024 * 1024):
                    handle.write(block)
                    digest.update(block)
                    size += len(block)
        actual = digest.hexdigest()
        expected = entry.get('lfs', {}).get('sha256')
        if size != entry['size'] or (expected and actual != expected):
            raise ValueError(f'MODEL_FILE_INTEGRITY:{name}')
        # Git blob IDs authenticate small config/tokenizer files too.
        if not expected:
            blob = hashlib.sha1(f'blob {size}\0'.encode() + partial.read_bytes()).hexdigest()
            if blob != entry['blobId']:
                raise ValueError(f'MODEL_GIT_BLOB_INTEGRITY:{name}')
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != actual:
                raise ValueError(f'IMMUTABLE_MODEL_FILE_EXISTS:{name}')
            # Keep duplicate partial as download evidence; never replace mismatched files.
        else:
            partial.rename(target)
        pins.append({'path': name, 'bytes': size, 'sha256': actual,
                     'upstream_lfs_sha256': expected, 'upstream_git_blob': entry['blobId']})
        print(name, size, actual, flush=True)
    identity = {'model_id': MODEL, 'revision': REVISION, 'license': metadata['cardData']['license'],
                'files': sorted(pins, key=lambda pin: pin['path'])}
    identity['bundle_digest'] = hashlib.sha256(json.dumps(identity, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()
    (out / 'model-manifest.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
