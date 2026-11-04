"""Bounded PDF text extraction in a separate process (no network or rendering)."""
import json
from pathlib import Path
import sys


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_DATA, (768 * 1024 * 1024, 768 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass
    sys.path.insert(0, str(Path(__file__).parent / '.deps'))
    try:
        from pypdf import PdfReader
    except ImportError:
        print(json.dumps({'error': 'PDF reader missing. Run python3 -m pip install --target .deps -r requirements.txt'}))
        return
    try:
        reader = PdfReader(sys.argv[1], strict=False)
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('Encrypted datasheet; open the original PDF to review it.')
        count = len(reader.pages)
        pages, used = [], 0
        for index in range(min(count, 80)):
            page = reader.pages[index]
            text = page.extract_text() or ''
            text = ''.join(c for c in text if c.isprintable() or c in '\n\t')
            text = text[:16000]
            if used + len(text) > 500000:
                break
            used += len(text)
            pages.append({'page': index + 1, 'text': text})
        print(json.dumps({'pages': pages, 'page_count': count,
                          'partial': len(pages) < count or any(len(p['text']) == 16000 for p in pages)}))
    except Exception:
        print(json.dumps({'error': 'This PDF could not be read reliably. Open the original datasheet.'}))


if __name__ == '__main__':
    main()
