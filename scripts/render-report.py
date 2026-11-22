"""Render a portable navigable report without network assets or dependencies."""
import argparse
import html
from pathlib import Path
import re
import unicodedata


def inline(value):
    value = html.escape(value)
    value = re.sub(r'`([^`]+)`', r'<code>\1</code>', value)
    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', value)
    return value


def render(source):
    lines = source.read_text(encoding='utf-8').splitlines()
    blocks, toc, used = [], [], set()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        heading = re.match(r'^(#{1,6}) (.+)', line)
        if heading:
            level, title = len(heading[1]), heading[2]
            slug = re.sub(r'[^a-z0-9]+', '-', unicodedata.normalize('NFKD', title).encode('ascii','ignore').decode().lower()).strip('-')
            identifier = slug
            while identifier in used:
                identifier += '-section'
            used.add(identifier)
            if level == 2:
                toc.append(f'<a href="#{identifier}">{inline(title)}</a>')
            blocks.append(f'<h{level} id="{identifier}">{inline(title)}</h{level}>')
        elif line.startswith('|'):
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append([cell.strip() for cell in lines[index].strip().strip('|').split('|')])
                index += 1
            header = ''.join(f'<th scope="col">{inline(cell)}</th>' for cell in rows[0])
            body = ''.join('<tr>'+''.join(f'<td>{inline(cell)}</td>' for cell in row)+'</tr>' for row in rows[2:])
            blocks.append(('<div class="table-scroll" tabindex="0" role="region" aria-label="Tabla desplazable"><table><thead><tr>'+header+'</tr></thead><tbody>'+body+'</tbody></table></div>').replace('Tabla desplazable','Tabla '+str(index)))
            continue
        elif re.match(r'^(?:\d+\. |\- )', line):
            ordered = bool(re.match(r'^\d+\.', line))
            rows = []
            pattern = r'^\d+\. ' if ordered else r'^- '
            while index < len(lines) and re.match(pattern, lines[index].strip()):
                rows.append('<li>'+inline(re.sub(pattern, '', lines[index].strip()))+'</li>')
                index += 1
            tag = 'ol' if ordered else 'ul'
            blocks.append(f'<{tag}>'+''.join(rows)+f'</{tag}>')
            continue
        else:
            paragraph = [line]
            while index+1 < len(lines) and lines[index+1].strip() and not re.match(r'^(?:#|\||\d+\. |\- )',lines[index+1].strip()):
                index += 1
                paragraph.append(lines[index].strip())
            blocks.append('<p>'+inline(' '.join(paragraph))+'</p>')
        index += 1
    style = '''
:root { color-scheme:light; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:#1c2e43; background:#f4f6fa; line-height:1.65; }
* { box-sizing:border-box; } body { margin:0; } a { color:#075985; text-underline-offset:.2em; } a:focus-visible,[tabindex]:focus-visible { outline:3px solid #b45309; outline-offset:4px; }
.skip { position:absolute; left:-9999px; } .skip:focus { left:1rem; top:1rem; background:white; padding:1rem; z-index:10; }
.layout { display:grid; grid-template-columns:255px minmax(0,1000px); gap:36px; max-width:1380px; padding:32px; margin:auto; }
nav { position:sticky; top:24px; align-self:start; max-height:calc(100vh - 48px); overflow:auto; padding:20px; background:#e7eef5; border-radius:12px; } nav strong { display:block; font-size:1.1rem; margin-bottom:12px; } nav a { display:block; margin:10px 0; font-size:.92rem; }
main { min-width:0; background:white; padding:40px; border-radius:16px; border:1px solid #d4deea; } .eyebrow { color:#425b75; font-size:.82rem; font-weight:700; letter-spacing:.09em; text-transform:uppercase; }
h1 { font-size:clamp(1.8rem,3.5vw,2.6rem); line-height:1.18; letter-spacing:-.025em; margin:18px 0 28px; } h2 { font-size:1.5rem; margin:3rem 0 1rem; padding-top:1rem; border-top:2px solid #dce6f0; scroll-margin-top:24px; line-height:1.3; } p { margin:1rem 0; } li { padding-left:5px; margin:.5rem 0; }
.table-scroll { overflow:auto; margin:20px 0; border:1px solid #c8d5e3; border-radius:8px; } table { border-collapse:collapse; width:100%; font-size:.91rem; } th,td { padding:12px 14px; border-bottom:1px solid #d6e1eb; text-align:left; vertical-align:top; min-width:135px; } th { background:#e7eef5; font-weight:700; } tbody tr:last-child td { border:0; } tbody tr:nth-child(even) { background:#f7f9fc; }
code { font-size:.9em; overflow-wrap:anywhere; } footer { margin-top:3rem; border-top:1px solid #ccd8e4; padding-top:16px; font-size:.9rem; color:#425b75; }
@media(max-width:850px) { .layout { display:block; padding:12px; } nav { position:static; max-height:none; margin-bottom:16px; padding:18px; } nav a { display:inline-block; margin:6px 12px 6px 0; } main { padding:24px 18px; } h2 { margin-top:2rem; } }
@media print { .layout { display:block; padding:0; } nav,.skip { display:none; } main { border:0; padding:0; } .table-scroll { overflow:visible; } h2 { break-after:avoid; } }
'''
    title = next((line[2:] for line in lines if line.startswith('# ')), 'Informe')
    task_id = source.parent.name
    document = '<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>'+html.escape(title)+'</title><style>'+style+'</style></head><body><a class="skip" href="#contenido">Saltar al contenido</a><div class="layout"><nav aria-label="Secciones del informe"><strong>'+html.escape(task_id)+'</strong>'+''.join(toc)+'</nav><main id="contenido"><div class="eyebrow">Informe técnico · evidencias y límites de validación</div>'+''.join(blocks)+'<footer><a href="'+html.escape(source.name)+'">Versión Markdown del informe</a> · Documento autónomo, sin recursos externos.</footer></main></div></body></html>\n'
    output = source.with_suffix('.html')
    output.write_text(document,encoding='utf-8')
    print(f'Rendered {output.name}: {len(toc)} sections')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    render(parser.parse_args().source)
