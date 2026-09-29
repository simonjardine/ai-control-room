"""Local session storage and host-edited memory. No automatic model-written memory."""
import copy
import json
import os
from pathlib import Path
from datetime import datetime
from uuid import uuid4


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def read_json(path, default):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError): return copy.deepcopy(default)


def valid_entries(entries):
    return [copy.deepcopy(e) for e in entries if isinstance(e, dict)
            and isinstance(e.get('channel'), str) and isinstance(e.get('speaker'), str)
            and isinstance(e.get('text'), str)]


class RoomStore:
    def __init__(self, root):
        self.root = Path(root)
        self.folder = self.root/'chats'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.pointer = self.folder/'active.json'
        self.memory_path = self.root/'room_memory.json'

    def fresh(self, topic='Open conversation', use_memory=True):
        now = datetime.now().isoformat()
        return dict(id=uuid4().hex, created=now, updated=now, topic=topic,
                    entries=[], recaps={}, use_memory=use_memory, channel='public')

    def path(self, sid):
        if not isinstance(sid,str) or len(sid)!=32 or any(c not in '0123456789abcdef' for c in sid):
            raise ValueError('Invalid chat identifier')
        return self.folder/(sid+'.json')

    def save(self, session):
        session['updated'] = datetime.now().isoformat()
        write_json(self.path(session['id']), session)
        write_json(self.pointer, dict(id=session['id']))

    def load(self, sid):
        data = read_json(self.path(sid), None)
        if not isinstance(data,dict) or data.get('id')!=sid or not isinstance(data.get('entries'),list):
            raise ValueError('Chat could not be read')
        data['entries'] = valid_entries(data['entries'])
        if not isinstance(data.get('recaps'),dict): data['recaps']={}
        return data

    def last(self):
        try: return self.load(read_json(self.pointer,{}).get('id'))
        except (ValueError,TypeError): return None

    def history(self):
        rows=[]
        for path in self.folder.glob('*.json'):
            if path.name=='active.json':continue
            data=read_json(path,{})
            if isinstance(data,dict) and isinstance(data.get('entries'),list) and data.get('id')==path.stem:
                rows.append(dict(id=data['id'], title=self.title(data), updated=data.get('updated',''),
                                 count=len(data['entries']), path=path))
        # Original room replays are offered explicitly; race replays are excluded.
        imported=set()
        for r in rows:
            data=read_json(r['path'],{})
            imported.add(data.get('imported_from'))
            imported.update(data.get('replay_files',[]))
        for path in (self.root/'replays').glob('room_*.jsonl'):
            if path.name not in imported:
                rows.append(dict(id=None,title='Earlier room transcript',updated=path.stem[5:],count=None,path=path))
        return sorted(rows,key=lambda r:str(r['updated']),reverse=True)

    @staticmethod
    def title(data):
        # Public previews only: private messages never appear in the history list.
        public=next((e['text'] for e in data['entries'] if e.get('channel')=='public' and e.get('speaker')=='human'),'')
        return (public or data.get('topic') or 'Private conversation')[:90].replace('\n',' ')

    def import_replay(self,path):
        path=Path(path).resolve()
        if path.parent!=(self.root/'replays').resolve() or not path.name.startswith('room_') or path.suffix!='.jsonl':
            raise ValueError('Not a room transcript')
        entries=[]
        for line in path.read_text(encoding='utf-8').splitlines():
            try:entries.extend(valid_entries([json.loads(line)]))
            except ValueError:continue # Preserve complete messages after an interrupted write.
        data=self.fresh();data['entries']=entries;data['imported_from']=path.name
        return data

    def memories(self):
        data=read_json(self.memory_path,{})
        return {k:v for k,v in data.items() if isinstance(k,str) and isinstance(v,str)} if isinstance(data,dict) else {}

    def save_memories(self,data):
        write_json(self.memory_path,data)


def memory_key(channel,specs):
    if channel=='public':return 'public'
    spec=specs[channel]
    return json.dumps([channel,spec['provider'],spec['model']],separators=(',',':'))
