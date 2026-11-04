"""Render a cited PDF passage in a bounded child process. No network."""
import json
from pathlib import Path
import sys
import unicodedata
sys.path.insert(0,str(Path(__file__).parent/'.deps'))


def normalized(text):
    chars=[];indexes=[]
    for index,char in enumerate(text):
        for c in unicodedata.normalize('NFKC',char).casefold():
            if c.isalnum():chars.append(c);indexes.append(index)
    return ''.join(chars),indexes


def render(path,page_number,quote,output):
    import pypdfium2 as pdf
    from PIL import ImageDraw
    doc=pdf.PdfDocument(path)
    if not 1<=page_number<=len(doc):raise ValueError('The cited page is not in the PDF.')
    page=doc[page_number-1];width,height=page.get_size()
    if not 0<width<=2000 or not 0<height<=2000:raise ValueError('PDF page size is unsupported.')
    textpage=page.get_textpage();text=textpage.get_text_range()
    haystack,indexes=normalized(text);needle,_=normalized(quote)
    start=haystack.find(needle) if len(needle)>=12 else -1
    matched=start>=0 and haystack.find(needle,start+1)<0
    boxes=[]
    # A table/figure title is a locator, not the requirement itself. Keep its
    # contents visible rather than presenting a highlighted heading alone.
    section_title=quote.strip().casefold().startswith(('table ', 'figure '))
    if matched:
        a,b=indexes[start],indexes[start+len(needle)-1]+1
        for index in range(a,b):
            box=textpage.get_charbox(index)
            if box[2]>box[0] and box[3]>box[1]:boxes.append(box)
    matched=bool(boxes)
    if matched and not section_title:
        # Keep neighbouring table columns (pin numbers, names, units) in view.
        # A quote-only crop can omit the information that binds it to the part.
        left,right=0,width
        bottom=max(0,min(b[1] for b in boxes)-12);top=min(height,max(b[3] for b in boxes)+55)
    else:left,bottom,right,top=0,0,width,height
    scale=min(2,1500/max(right-left,top-bottom))
    bitmap=page.render(scale=scale,crop=(left,bottom,width-right,height-top))
    image=bitmap.to_pil().convert('RGB')
    if matched:
        draw=ImageDraw.Draw(image,'RGBA')
        for x1,y1,x2,y2 in boxes:
            draw.rectangle(((x1-left)*scale,(top-y2)*scale,(x2-left)*scale,(top-y1)*scale),fill=(255,208,60,65))
    image.save(output,'PNG')
    result={'matched':matched,'page':page_number,'width':image.width,'height':image.height,
            'caption':('Full cited page, including the table or figure below the highlighted heading.' if section_title else 'Highlighted text matched the cited passage.') if matched else 'Full cited page. The exact passage could not be located automatically.'}
    textpage.close();page.close();doc.close()
    return result


if __name__=='__main__':
    try:
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU,(20,20))
        except ImportError:
            pass  # Windows has no `resource`; run unbounded like other platforms' fallback.
        request=json.load(sys.stdin)
        print(json.dumps(render(request['path'],request['page'],request['quote'],request['output'])))
    except Exception as exc:print(json.dumps({'error':str(exc)}))
