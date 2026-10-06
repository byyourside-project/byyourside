"""Read a script as document text, never as executable instructions."""
import io
import zipfile
from xml.etree import ElementTree

MAX_SCRIPT_BYTES = 10 * 1024 * 1024
MAX_SCRIPT_CHARS = 12000
SCRIPT_EXTENSIONS = {'.txt', '.md', '.pdf', '.docx'}


def extract_script(payload, extension):
    extension = extension.lower()
    if extension not in SCRIPT_EXTENSIONS:
        raise ValueError('대본은 TXT, MD, PDF, DOCX 파일로 올려 주세요.')
    if not payload or len(payload) > MAX_SCRIPT_BYTES:
        raise ValueError('대본 파일은 비어 있지 않은 10MB 이하 파일이어야 합니다.')
    warnings = []
    if extension in {'.txt', '.md'}:
        encodings = ['utf-16'] if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'cp949']
        text = None
        for encoding in encodings:
            try:
                text = payload.decode(encoding)
                break
            except UnicodeError:
                pass
        if text is None or '\x00' in text:
            raise ValueError('대본의 문자 형식을 읽지 못했습니다. UTF-8 TXT로 저장해 주세요.')
    elif extension == '.pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(payload))
            if reader.is_encrypted:
                raise ValueError('암호가 없는 PDF 대본을 사용해 주세요.')
            if len(reader.pages) > 100:
                raise ValueError('PDF 대본은 100쪽 이하로 올려 주세요.')
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or '')
                if sum(map(len, pages)) > MAX_SCRIPT_CHARS:
                    raise ValueError('대본은 12,000자 이하로 줄여 주세요.')
            text = '\n\n'.join(pages)
            warnings.append('PDF 줄바꿈과 읽는 순서를 확인하고 비교할 문장별로 정리해 주세요.')
        except ImportError as exc:
            raise ValueError('PDF 읽기 기능 설치가 필요합니다. TXT 대본을 사용하거나 실행 안내를 확인해 주세요.') from exc
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('PDF 대본을 읽지 못했습니다. 텍스트를 복사해서 입력해 주세요.') from exc
    else:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as doc:
                item = doc.getinfo('word/document.xml')
                if item.file_size > 3 * 1024 * 1024:
                    raise ValueError('문서의 본문이 너무 큽니다. 대본 부분만 별도 파일로 저장해 주세요.')
                xml = doc.read(item)
                if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                    raise ValueError('지원하지 않는 문서 형식입니다.')
                root = ElementTree.fromstring(xml)
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                pieces = []
                for paragraph in root.findall('.//w:p', ns):
                    words = []
                    for node in paragraph.iter():
                        local = node.tag.rsplit('}', 1)[-1]
                        if local == 't':
                            words.append(node.text or '')
                        elif local in {'br', 'cr'}:
                            words.append('\n')
                        elif local == 'tab':
                            words.append('\t')
                    pieces.append(''.join(words))
                text = '\n'.join(pieces)
        except ValueError:
            raise
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
            raise ValueError('DOCX 대본을 읽지 못했습니다. TXT로 저장하거나 본문을 붙여 넣어 주세요.') from exc
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        raise ValueError('대본에서 글자를 찾지 못했습니다. 스캔 PDF는 본문을 직접 입력해 주세요.')
    if len(text) > MAX_SCRIPT_CHARS:
        raise ValueError('대본은 12,000자 이하로 줄여 주세요.')
    if len([line for line in text.splitlines() if line.strip()]) > 100:
        warnings.append('줄바꿈이 많습니다. 대본 입력란에서 100묶음 이하로 정리해 주세요.')
    return {'text': text, 'warnings': warnings}
