"""Lists every Russian text fragment of index.html (the source page) in order of first appearance."""
import re, sys, json
FRAG = re.compile(r"[А-Яа-яЁё](?:[А-Яа-яЁё0-9 ,.:;!?«»()\-–—%/+…№]*[А-Яа-яЁё.!?»)…:])?")
def fragments(text):
    seen, out = set(), []
    for m in FRAG.finditer(text):
        f = m.group(0)
        if f not in seen:
            seen.add(f); out.append(f)
    return out
if __name__ == '__main__':
    fr = fragments(open(sys.argv[1], encoding='utf-8').read())
    json.dump(fr, open(sys.argv[2], 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print(len(fr), 'fragments,', sum(map(len, fr)), 'chars')
