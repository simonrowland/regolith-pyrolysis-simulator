"""Preserve bulletin summary substance blocks; never repair OCR tokens.

Input pages are pdftotext -layout output. Independent Tesseract raster text is
used only as a disagreement detector, never as a replacement transcription.
"""
import concurrent.futures
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
SOURCE = Path('/Users/simonrowland/Repos/regolith-corpus-ctl/raw/robie-hemingway-1995-usgs-b2131/robie-hemingway-1995-usgs-b2131.pdf')
TMP = Path('/private/tmp/b2131-summary-raster')
NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z')


def raster(page):
    stem = TMP / str(page)
    if not stem.with_suffix('.txt').exists() or not stem.with_suffix('.txt').stat().st_size:
        subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-r', '120', '-singlefile', '-png', str(SOURCE), str(stem)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['tesseract', str(stem.with_suffix('.png')), str(stem), '--psm', '6'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return stem.with_suffix('.txt').read_text()


def cell(raw, raster_text):
    raw = raw.strip()
    value = float(raw) if NUMBER.fullmatch(raw) else None
    # Exact token agreement only. Repeated equal values do not establish row
    # alignment; consequently agreement remains suspect rather than certified.
    agreement = bool(raw) and re.search(r'(?<![\w.])' + re.escape(raw) + r'(?![\w.])', raster_text) is not None
    return {'raw': raw, 'value': value, 'ocr_suspect': bool(raw),
            'ocr_check': 'machine_token_agreement_row_unverified' if agreement else 'machine_disagreement' if raw else 'blank'}


def harvest(page, raster_text):
    text = Path(f'/tmp/b2131-census/{page:03}.txt').read_text()
    lines = text.splitlines()
    cp = page >= 47
    header_idx = next(i for i, line in enumerate(lines) if 'ENTROPY' in line and ('NAME' in line or 'FOR' in line or 'NNE' in line or 'IWE' in line or 'tiNE' in line or 'twE' in line))
    heading = lines[header_idx]
    first_numeric = heading.index('ENTROPY') if cp else next(m.start() for m in re.finditer(r'WEIGHT|\\lElGHT', heading))
    # A substance begins with a left label and multiple-column spacing before
    # its first number. Formula and uncertainty lines remain inside the block.
    starts = []
    for i in range(header_idx + 1, len(lines)):
        line = lines[i]
        if not line[:max(1, first_numeric - 2)].strip():
            continue
        if re.search(r'\S\s{2,}[+\-·\d](?:[\d.]*)', line) and not re.search(r'J\s*[•·]|kJ|mol|mor|\(T', line):
            if i == 0 or not lines[i - 1].strip():
                starts.append(i)
    if not starts:
        return [], {'pdf_page': page, 'printed_page': page-6, 'reason': 'No defensible substance boundaries recovered'}
    if not cp:
        for i in range(header_idx+1, len(lines)):
            match = re.search(r'\s{2,}([0-9]+\.[0-9]+)', lines[i])
            if match and first_numeric - 6 <= match.start(1) <= first_numeric + 7 and lines[i][:match.start()].strip():
                starts.append(i)
            anonymous=re.match(r'\s*([0-9][^\s]*)',lines[i])
            if anonymous and first_numeric-6 <= anonymous.start(1) <= first_numeric+7 and not lines[i-1].strip() and len(re.findall(r'\d+\.\d+',lines[i]))>=3:
                starts.append(i)
        starts = sorted(set(starts))
    else:
        for i in range(starts[0],len(lines)):
            line=lines[i]
            match=re.search(r'\s{2,}([0-9]+\.[0-9]+)',line)
            if match and first_numeric-3 <= match.start(1) <= first_numeric+10 and re.search(r'[Ee£][+·-][0-9]',line) and line[:match.start()].strip():
                starts.append(i)
        starts=sorted(set(starts))
    header = '\n'.join(lines[header_idx:starts[0]])
    title=next(l.strip() for l in lines if ('COEFFICIENTS FOR' if cp else 'THERMODYNAMIC PROPERTIES O') in l and ('MINERALS AND RELATED' not in l))
    printed_temperature=re.search(r'AT\s+(\S+)\s+K',title) if not cp else None
    temperature_cell=cell(printed_temperature.group(1),raster_text) if printed_temperature else None
    patterns = [('entropy', 'ENTROPY'), ('A1', r'A1|~1'), ('A2', 'A2'), ('A3', 'A3'), ('A4', 'A4'), ('A5', r'A5|AS'), ('temperature_range', r'T\s*r|Tr'), ('transition_temperature', r'T[.•_]|T[a-zA-Z]')] if cp else [('weight', r'WEIGHT|\\lElGHT'), ('entropy', 'ENTROPY'), ('volume', r'VO\S*'), ('formation_enthalpy', 'ENTHALPY'), ('formation_gibbs', 'FREE'), ('log_kf', r'LOG\(K\)'), ('references', r'REFER\S*')]
    centers = []
    for column, pattern in patterns:
        matches = [(m.start() + len(m.group())/2) for line in lines[header_idx:starts[0]] for m in re.finditer(pattern, line) if m.start() >= first_numeric]
        matches = [x for x in matches if not centers or x > centers[-1][1]+3]
        if matches: centers.append((column, min(matches)))
    if cp and len(centers)==7:
        units=[m for line in lines[header_idx:starts[0]] for m in re.finditer(r'(?<!\S)(?:IC|K)(?!\S)',line) if m.start()>centers[-1][1]+3]
        if len(units)==1: centers.append(('transition_temperature',(units[0].start()+units[0].end())/2))
    if cp and len(centers)==8:
        centers.append(('transition_enthalpy', max(len(l.rstrip()) for l in lines[header_idx:starts[0]])-3))
    if not cp:
        refs_line = next((l for l in lines[header_idx:starts[0]] if 'H/G' in l), '')
        refs = [m for m in re.finditer(r'(?<!\S)(?:s|S|H/G|c|C)(?!\S)', refs_line) if m.start()>centers[-2][1]]
        if len(refs)==3:
            centers=centers[:-1]+[(key,(m.start()+m.end())/2) for key,m in zip(('reference_entropy','reference_enthalpy_gibbs','reference_heat_capacity'),refs)]
    records=[]
    for ordinal, start in enumerate(starts):
        end = starts[ordinal+1] if ordinal+1<len(starts) else len(lines)
        block = lines[start:end]
        while block and not block[-1].strip(): block.pop()
        record_id=f'{"cp" if cp else "reference"}-p{page-6:03}-{ordinal+1:02}'
        name=re.split(r'\s{2,}[+\-·\d]',block[0],maxsplit=1)[0].strip()
        formula=None
        token_rows = []
        for line_index,line in enumerate(block):
            if line.strip():
                tokens = []
                for match in re.finditer(r'\S+', line):
                    token = cell(match.group(), raster_text)
                    token['start_offset'] = match.start()
                    token['end_offset'] = match.end()
                    tokens.append(token)
                token_rows.append({'raw': line, 'tokens': tokens,'source_line_index':line_index})
        semantic_rows=[]
        issues=[]
        cutoff=first_numeric-5
        for row in token_rows:
            words=[m for m in re.finditer(r'\S+', row['raw']) if m.start() >= cutoff]
            groups={key:[] for key,_ in centers}
            for word in words:
                midpoint=(word.start()+word.end())/2
                column, center=min(centers,key=lambda pair:abs(midpoint-pair[1]))
                groups[column].append(word)
            cells={}
            for column,_ in centers:
                spans=groups[column]
                raw=row['raw'][spans[0].start():spans[-1].end()] if spans else ''
                cells[column]=cell(raw,raster_text)
                cells[column]['start_offset']=spans[0].start() if spans else None
                cells[column]['end_offset']=spans[-1].end() if spans else None
                if raw and cells[column]['value'] is None:
                    issues.append(f'{column}: unparsed printed OCR token {raw!r}')
            refkeys=[key for key in cells if key.startswith('reference_')]
            if any(len(cells[key]['raw'].split())>1 for key in refkeys):
                populated=[cells[key] for key in refkeys if cells[key]['raw']]
                lo=min(c['start_offset'] for c in populated)
                hi=max(c['end_offset'] for c in populated)
                merged=cell(row['raw'][lo:hi],raster_text)
                merged.update({'value':None,'start_offset':lo,'end_offset':hi,'spans_columns':refkeys,'column_assignment':'unresolved OCR column collapse'})
                for key in refkeys: del cells[key]
                cells['references_unresolved']=merged
                issues.append(f'Reference columns collapsed by OCR at source line {row["source_line_index"]+1}; preserve raw span without assigning reference IDs to S/HG/C')
            semantic_rows.append({'label_raw':row['raw'][:cutoff].strip(),'cells':cells,'source_line_index':row['source_line_index']})
        if len(centers)!=9: issues.append('Incomplete column header geometry')
        numeric_first=semantic_rows[0]['cells'] if semantic_rows else {}
        if not numeric_first.get('entropy',{}).get('value'): issues.append('First-row entropy unresolved')
        # A blank cell is preserved, but body letters beyond the label boundary
        # indicate a name/formula collided with the numeric region.
        bad_geometry=any(re.search(r'[A-DF-Za-df-z]{2}', c['raw']) for r in semantic_rows for key,c in r['cells'].items())
        merged_numeric=[(r['source_line_index']+1,key,c['raw']) for r in semantic_rows for key,c in r['cells'].items() if key!='references_unresolved' and len(c['raw'].split())>1]
        swallowed_numeric=bool(re.search(r'\s{2,}[+-]?\d+\.\d+',semantic_rows[0]['label_raw']))
        if merged_numeric:
            bad_geometry=True
            issues.append('Multiple lexical tokens share a semantic field; split-token versus adjacent-column assignment cannot be established: '+repr(merged_numeric))
        if swallowed_numeric:
            bad_geometry=True
            issues.append('Primary numeric weight or coefficient falls inside the page-derived label boundary: '+repr(semantic_rows[0]['label_raw']))
        complete=not bad_geometry and len(centers)==9 and bool(semantic_rows[0]['label_raw'])
        if complete: name=semantic_rows[0]['label_raw']
        if not semantic_rows[0]['label_raw']: issues.append('Substance name and formula missing from primary OCR layer at this numeric block')
        if bad_geometry: issues.append('Body label or prose enters numeric region: '+repr([(key,c['raw']) for r in semantic_rows for key,c in r['cells'].items() if re.search(r'[A-DF-Za-df-z]{2}',c['raw'])]))
        phase=[p for p in re.findall(r'\([^)]*\)', ' '.join(r['label_raw'] for r in semantic_rows[:2])) if re.search(r'REFERENCE STATE|ION|LIQUID|GAS|crystal|ordered',p,re.IGNORECASE)]
        if len(semantic_rows)>1: formula=semantic_rows[1]['label_raw'] or None
        if 'AQUEOUS' in name or (formula and formula.startswith('STD.') and '(' in name):
            formula=name.split('(')[0].strip()
        elif formula:
            formula=re.sub(r'\s*\((?:[A-Za-z«·-]*crystal|LIQUID|liquid|IDEAL GAS|ideal gas)\)\s*$','',formula).strip()
        if formula and formula.startswith('STD.'):
            formula=None
        identity_checks=[]
        if not cp and complete:
            dg=numeric_first.get('formation_gibbs',{})
            logk=numeric_first.get('log_kf',{})
            if dg.get('value') is not None and logk.get('value') is not None:
                factor=8.31446261815324*temperature_cell['value']*math.log(10)/1000
                def half_step(raw):
                    return 0.5*10**(-len(raw.split('.')[1])) if '.' in raw else 0.5
                tolerance=half_step(dg['raw'])+factor*half_step(logk['raw'])+0.01
                residual=dg['value']+factor*logk['value']
                identity_checks.append({'identity':'delta_f_G + R*T*ln(10)*log_Kf = 0','residual_kJ_per_mol':residual,'rounding_tolerance_kJ_per_mol':tolerance,'disagreement':abs(residual)>tolerance})
                if abs(residual)>tolerance: issues.append(f'Formation Gibbs/log Kf identity disagreement: residual {residual} kJ/mol; tolerance {tolerance}; detector only, raw unchanged')
        records.append({'record_id':record_id,'table_kind':'heat_capacity_coefficients' if cp else 'reference_state_298K','pdf_page':page,'page':page-6,'table_number':None,'table_title_raw':next(l.strip() for l in lines if ('COEFFICIENTS FOR' if cp else 'THERMODYNAMIC PROPERTIES O') in l and ('MINERALS AND RELATED' not in l)), 'name_as_published': name if complete else None, 'formula_as_published':formula if complete else None,'phase_as_published':phase if complete else None,'name_candidate':name,'formula_candidate':formula,'header_and_units_raw':header,'source_text':'\n'.join(block),'rows':semantic_rows if complete else token_rows,'transcription_status':'transcribed_ocr_suspect' if complete else 'untranscribed','identity_checks':identity_checks,'ambiguities':issues+['All numerical tokens checked against independent raster OCR by exact page occurrence; row alignment remains unverified. No OCR repair.']+([] if complete else ['Column geometry or metadata collides with numeric fields; semantic assignment withheld.']),'temperature_grid':[] if cp else [{'raw':'298.15','value':298.15,'ocr_suspect':True,'ocr_check':'section_heading'}]})
        record=records[-1]
        record['temperature_grid']=[] if cp or temperature_cell is None else [temperature_cell]
        record['source_id']='robie-hemingway-1995-usgs-b2131'
        record['compilation_role']={'engine_reference_input':True,'validation_measurement':False,'scoring_eligible':False,'battery_refusal':'gibbs_table_not_runtime_observable'}
        record['source_line_start']=start+1
        record['source_line_end']=start+len(block)
        if complete:
            del record['name_candidate']
            del record['formula_candidate']
    return records, None


def main():
    TMP.mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        readings=dict(zip(range(11,73),pool.map(raster, range(11,73))))
    entries=[]; failures=[]; census=[]
    for page in range(11,73):
        records, failure=harvest(page, readings[page])
        census.append({'pdf_page':page,'printed_page':page-6,'substance_blocks':len(records),'baseline_lines':[r['source_line_start'] for r in records],'transcribed':sum(r['transcription_status']=='transcribed_ocr_suspect' for r in records),'untranscribed':sum(r['transcription_status']=='untranscribed' for r in records)})
        if failure: failures.append(failure)
        for record in records:
            dest=ROOT/(record['record_id']+'.json')
            dest.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
            entries.append({'record_id':record['record_id'],'path':'summary/'+dest.name,'pdf_page':page,'page':page-6,'name_as_published':record['name_as_published'],'formula_as_published':record['formula_as_published'],'row_count':len(record['rows']),'status':record['transcription_status'],'ambiguities':record['ambiguities']})
            print('SUMMARY TABLE '+record['record_id'],flush=True)
    (ROOT/'index.json').write_text(json.dumps({'entries':entries,'per_pdf_page_census':census,'untranscribed_records':[e for e in entries if e['status']=='untranscribed'],'untranscribed_pages':failures,'status':'ocr_transcription_with_unresolved_blocks','scope':{'reference_state_pdf_pages':[11,46],'heat_capacity_pdf_pages':[47,72]},'census_note':'Substance blocks counted directly from primary OCR numeric baselines, including unnamed blocks and missing blank separators. All numeric cells conservatively suspect; no OCR corrections. Page-level independent raster checks do not certify row alignment.'},ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
