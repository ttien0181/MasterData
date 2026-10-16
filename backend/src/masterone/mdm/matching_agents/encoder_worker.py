"""Isolated D1 executable. Fixed approved encoder; never imports backend services."""
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


def hash_file(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    root = Path(sys.argv[1]).resolve()
    lock = root / 'requirements-lock.txt'
    linux = sys.platform == 'linux'
    expected_lock = ('763682254cdd498b21bee141022097b0fa0ffa7956abb0913d3dfc13b9838a0f' if linux else
                     '291fea99bb0805521e318cfd69d2a2aca9fcb7d7d6341d62ba92b37a9c0c7141')
    if hash_file(lock) != expected_lock:
        raise ValueError('ENCODER_LOCK_MISMATCH')
    for entry in lock.read_text(encoding='utf-8').splitlines():
        name, version = entry.split('==', 1)
        if importlib.metadata.version(name) != version:
            raise ValueError('ENCODER_DEPENDENCY_VERSION_MISMATCH')
    if not ((linux and sys.version_info[:2] == (3, 11)) or
            (sys.platform == 'win32' and sys.version_info[:3] == (3, 13, 11))):
        raise ValueError('ENCODER_PLATFORM_NOT_APPROVED')
    manifest = json.loads((root / 'model-manifest.json').read_text(encoding='utf-8'))
    for entry in manifest['files']:
        path = (root / 'model' / entry['path']).resolve()
        if not path.is_relative_to((root / 'model').resolve()) or hash_file(path) != entry['sha256']:
            raise ValueError('ENCODER_MODEL_FILE_MISMATCH')
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(7)
    torch.use_deterministic_algorithms(True)
    model = SentenceTransformer(str(root / 'model'), device='cpu', local_files_only=True, trust_remote_code=False)
    model.float().eval()
    identity = {'model_id': 'intfloat/multilingual-e5-small', 'revision': manifest['revision'],
        'bundle_digest': manifest['bundle_digest'], 'dimension': 384, 'dtype': 'float32', 'device': 'cpu',
        'runtime_lock_sha256': hash_file(lock), 'worker_code_sha256': hash_file(Path(__file__)),
        'python': '3.11' if linux else '3.13.11', 'platform': sys.platform}
    print(json.dumps({'status': 'READY', 'identity': identity}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        field, texts = request['field'], request['texts']
        if field not in ('name', 'address') or not 0 < len(texts) <= 16:
            raise ValueError('ENCODER_BATCH_CONTRACT')
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError('ENCODER_MISSING_TEXT')
        model.max_seq_length = 128 if field == 'name' else 512
        inputs = ['query: ' + text for text in texts]
        input_tokens = int(model.tokenize(inputs)['attention_mask'].sum().item())
        vectors = model.encode(inputs, batch_size=16,
            normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
        print(json.dumps({'request_id': request['request_id'], 'field': field,
                          'vectors': vectors.tolist(), 'input_tokens': input_tokens}, allow_nan=False), flush=True)


def error_envelope(error):
    codes = {'ENCODER_LOCK_MISMATCH', 'ENCODER_DEPENDENCY_VERSION_MISMATCH',
             'ENCODER_PLATFORM_NOT_APPROVED', 'ENCODER_MODEL_FILE_MISMATCH'}
    code = str(error)
    integrity = isinstance(error, ValueError) and code in codes
    return {'error': code if integrity else 'ENCODER_' + type(error).__name__.upper(), 'integrity': integrity}


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(error_envelope(error)), flush=True)
        raise SystemExit(1) from None
