"""Record a verified plan step without changing unrelated checkboxes."""
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PLAN = Path('docs/superpowers/plans/2026-10-12-session-workflow-be-first-fe-second.vi.md')
p = argparse.ArgumentParser()
p.add_argument('checkpoint')
p.add_argument('step', type=int)
p.add_argument('result')
p.add_argument('evidence')
p.add_argument('--checkpoint-status')
a = p.parse_args()
content = PLAN.read_text(encoding='utf-8')
if a.step:
    start = content.index('### ' + a.checkpoint + ' —')
    next_heading = content.find('\n### ', start + 4)
    next_section = content.find('\n## ', start + 4)
    ends = [index for index in (next_heading, next_section) if index >= 0]
    end = min(ends) if ends else len(content)
    section = content[start:end]
    lines = section.splitlines(keepends=True)
    steps = [i for i, line in enumerate(lines) if line.startswith('- [')]
    i = steps[a.step - 1]
    if a.result == 'PASS':
        lines[i] = lines[i].replace('- [ ]', '- [x]', 1)
    content = content[:start] + ''.join(lines) + content[end:]
if a.checkpoint_status:
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith('| ' + a.checkpoint + ' |'):
            cols = line.split('|')
            cols[4] = ' ' + a.checkpoint_status + ' '
            cols[5] = ' ' + a.evidence.replace('|', '/') + ' '
            lines[i] = '|'.join(cols)
    content = ''.join(lines)
now = datetime.now(ZoneInfo('Asia/Bangkok')).strftime('%Y-%m-%d %H:%M')
entry = f'| {now} | {a.checkpoint}/{a.step} | {a.result} | {a.evidence.replace("|", "/")} |\n\n'
content = content.replace('## 7. Hai prompt thực thi', entry + '## 7. Hai prompt thực thi')
PLAN.write_text(content, encoding='utf-8')
