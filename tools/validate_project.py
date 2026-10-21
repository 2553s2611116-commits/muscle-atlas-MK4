#!/usr/bin/env python3
"""Offline structural/data checks for MUSCLE ATLAS Mark 4. Uses only Python stdlib."""
from __future__ import annotations
import json, re, sys, subprocess, tempfile, base64, struct, math, xml.etree.ElementTree as ET
from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'app' / 'src' / 'main' / 'assets'
DATA_PATH = ASSETS / 'data.json'
HTML_PATH = ASSETS / 'index.html'
REQUIRED_FIELDS = {
    'name': str, 'type': str, 'body': str, 'main': list, 'sub': list,
    'level': str, 'equipment': str, 'form': str, 'tip': str,
    'related': list, 'goals': list, 'id': str, 'family': str, 'variation': str,
}

class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(); self.scripts=[]; self.current=None
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script':
            self.current={'attrs':dict(attrs),'text':[]}; self.scripts.append(self.current)
    def handle_data(self, data):
        if self.current is not None: self.current['text'].append(data)
    def handle_endtag(self, tag):
        if tag.lower() == 'script': self.current=None

def die(msg):
    print('FAIL:', msg, file=sys.stderr); sys.exit(1)
def check(condition, msg):
    if not condition: die(msg)
    print('PASS:', msg)

for relative in ['settings.gradle','build.gradle','gradle.properties','.github/workflows/build-apk.yml',
                 'app/build.gradle','app/src/main/AndroidManifest.xml',
                 'app/src/main/java/com/muscleatlas/app/MainActivity.java',
                 'app/src/main/assets/data.json','app/src/main/assets/index.html']:
    check((ROOT/relative).is_file(), f'project file exists: {relative}')
for xml_path in [ROOT/'app/src/main/AndroidManifest.xml', *sorted((ROOT/'app/src/main/res').rglob('*.xml'))]:
    try: ET.parse(xml_path)
    except Exception as exc: die(f'Android XML is invalid ({xml_path.relative_to(ROOT)}): {exc}')
check(True, 'Android manifest and XML resources parse successfully')

try:
    db=json.loads(DATA_PATH.read_text(encoding='utf-8'))
except Exception as exc: die(f'data.json is not valid JSON: {exc}')
check(isinstance(db,dict) and isinstance(db.get('exercises'),list) and isinstance(db.get('tips'),list), 'dataset has exercises and tips arrays')
exercises=db['exercises']
check(len(exercises)==1500, f'exercise count is exactly 1,500 (found {len(exercises)})')
ids=[]; names=[]; missing=[]
for i,e in enumerate(exercises):
    if not isinstance(e,dict): die(f'exercise #{i+1} is not an object')
    for field,typ in REQUIRED_FIELDS.items():
        if field not in e or not isinstance(e[field],typ): missing.append(f'#{i+1}:{field}')
        elif typ is str and not e[field].strip(): missing.append(f'#{i+1}:{field}(empty)')
    ids.append(e.get('id')); names.append(e.get('name'))
    if any(not isinstance(e.get(k),list) for k in ('main','sub','related','goals')): continue
if missing: die('missing/wrong required fields: '+', '.join(missing[:12]))
check(all(len(v)==len(set(v)) for v in [ids]), 'exercise IDs are unique')
check(all(len(v)==len(set(v)) for v in [names]), 'exercise names are unique')
check(all(all(isinstance(x,str) and x.strip() for x in e[k]) for e in exercises for k in ('main','sub','related','goals')), 'muscle/related/goals tags are non-empty strings')
check(all(e['body'] in {'가슴','등','어깨','팔','하체','코어','전신'} for e in exercises), 'all exercise body categories use the supported taxonomy')
check(all(e['type'] in {'맨몸','프리웨이트','기능성','머신','재활·가동성'} for e in exercises), 'all exercise methods use the supported taxonomy')
check(all(e['level'] in {'초급','중급','상급'} for e in exercises), 'all difficulty values use the supported taxonomy')
name_set=set(names)
unknown_related=[(e['name'],r) for e in exercises for r in e['related'] if r not in name_set]
check(not unknown_related, f'all related-exercise links resolve to a dataset item (missing {len(unknown_related)})')
check(all(e['family'] in name_set for e in exercises), 'all exercise variants resolve to a base family present in the dataset')
check(sum(e['variation']=='기본 동작' for e in exercises)==200, 'dataset retains 200 base exercise entries alongside variants')

html=HTML_PATH.read_text(encoding='utf-8')
parser=ScriptCollector(); parser.feed(html)
json_scripts=[s for s in parser.scripts if s['attrs'].get('type','').lower()=='application/json' and s['attrs'].get('id')=='ma-data']
check(len(json_scripts)==1, 'exactly one embedded ma-data JSON block')
try: embedded=json.loads(''.join(json_scripts[0]['text']))
except Exception as exc: die(f'embedded JSON is invalid: {exc}')
check(embedded==db, 'embedded HTML exercise data exactly matches data.json')
mesh_scripts=[s for s in parser.scripts if s['attrs'].get('type','').lower()=='application/json' and s['attrs'].get('id')=='ma-meshdata']
check(len(mesh_scripts)==1, 'exactly one inline offline 3D mesh data block')
try: mesh_data=json.loads(''.join(mesh_scripts[0]['text']))
except Exception as exc: die(f'embedded 3D mesh JSON is invalid: {exc}')
expected_meshes={'stand','squat','hinge','lunge','floor','bench','pull','row','overhead','arm'}
check(set(mesh_data)==expected_meshes, '10 shared low-poly pose meshes are embedded')
for mesh_key,payload in mesh_data.items():
    try: raw=base64.b64decode(payload,validate=True)
    except Exception as exc: die(f'{mesh_key} mesh base64 is invalid: {exc}')
    if len(raw)<8: die(f'{mesh_key} mesh header is truncated')
    nv,nf=struct.unpack_from('<II',raw,0)
    pos_off=8; norm_off=pos_off+nv*12; idx_off=norm_off+nv*12
    expected_len=idx_off+nf*3*2
    if nv<500 or nf<500 or nv>=65536 or len(raw)!=expected_len: die(f'{mesh_key} mesh size/count invalid: vertices={nv}, faces={nf}, bytes={len(raw)}')
    for offset in (pos_off,norm_off):
        vals=struct.iter_unpack('<f',raw[offset:offset+nv*12])
        if not all(math.isfinite(v[0]) for v in vals): die(f'{mesh_key} mesh contains non-finite coordinates/normals')
    max_index=max(v[0] for v in struct.iter_unpack('<H',raw[idx_off:]))
    if max_index>=nv: die(f'{mesh_key} mesh has out-of-range triangle index')
check(True, 'all 3D mesh buffers decode, contain finite coordinates and valid indices')
check('DB.exercises.length' in html and '개' in html, 'UI derives exercise count from the database')
check('maInitCanvas' in html and 'maRender' in html and 'maFallbackMarkup' in html and 'maUploadBodyMesh' in html, '3D canvas, smooth mesh loader and no-WebGL fallback handlers are present')
check(all(key in html for key in ['changeLibraryPage','function toggleSave','function saveRoutine','function exportBackup','function importBackup']), 'search/list/routine/save/backup handlers remain present')
check('function toggleDraftExerciseByIndex(index,checked)' in html and 'toggleDraftExerciseByIndex(${DB.exercises.indexOf(e)},this.checked)' in html, 'routine picker uses safe numeric exercise indexes instead of broken nested string quotes')
check('function markRoutineDone(index,checked)' in html and 'markRoutineDone(${DB.exercises.indexOf(e)},this.checked)' in html, 'routine completion checkboxes use safe exercise indexes')
check('window.__maStorageUnavailable=true' in html and 'function persist(){try{' in html, 'storage-denied contexts do not interrupt UI event flows')
check('h.date===localDateKey()' in html and "'checked':''" in html, 'today-completed routine exercises render with their saved checked state')
check('toggleDraftExercise(${JSON.stringify(e.name)}' not in html and 'name:${JSON.stringify(e.name)}' not in html, 'no exercise name is interpolated into a quoted inline event handler')
# The app should be self-contained for offline use. External connections in user content aren't used by the runtime.
check(not re.search(r'<script[^>]+src\s*=\s*["\']https?://',html,re.I), 'no remotely loaded JavaScript')
check(not re.search(r'<link[^>]+href\s*=\s*["\']https?://',html,re.I), 'no remotely loaded stylesheet')

build= (ROOT/'app/build.gradle').read_text(encoding='utf-8')
check("applicationId 'com.muscleatlas.mark4'" in build and "versionName '4.0.0'" in build, 'Mark 4 has separate application ID and version')
manifest=(ROOT/'app/src/main/AndroidManifest.xml').read_text(encoding='utf-8')
check('android:hardwareAccelerated="true"' in manifest, 'Android WebView hardware acceleration is explicitly enabled for WebGL')
workflow=(ROOT/'.github/workflows/build-apk.yml').read_text(encoding='utf-8')
check('python tools/validate_project.py' in workflow, 'GitHub Actions runs validator before build')
check('name: muscle-atlas-mark4-apk' in workflow and 'app-debug.apk' in workflow, 'workflow uploads expected APK artifact')

# Syntax check actual inline JavaScript script(s) with Node if present.
js_scripts=[]
for s in parser.scripts:
    if s['attrs'].get('type','').lower()=='application/json': continue
    code=''.join(s['text']).strip()
    if code: js_scripts.append(code)
check(len(js_scripts)>=1, 'inline application JavaScript is present')
node=subprocess.run(['node','--version'],capture_output=True,text=True)
if node.returncode==0:
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',suffix='.js',delete=False) as f:
        f.write('\n'.join(js_scripts)); temp=Path(f.name)
    try:
        result=subprocess.run(['node','--check',str(temp)],capture_output=True,text=True)
        if result.returncode: die('JavaScript syntax check failed: '+(result.stderr or result.stdout)[:2000])
        check(True, 'inline JavaScript passes node --check')
    finally: temp.unlink(missing_ok=True)
else:
    print('WARN: Node.js unavailable; skipped JavaScript syntax check')

print(f'OK: structural validation complete for {len(exercises)} database entries. Android Gradle build must still be confirmed by GitHub Actions.')
