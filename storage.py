"""SQLite index and review journal. Large report payloads remain immutable files."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading


class RevisionConflict(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class WorkspaceStore:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'.preflight.sqlite3'
        self._preference_cache={}
        self._preference_lock=threading.Lock()
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL COLLATE NOCASE,
                    created TEXT NOT NULL, summary TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                    source_uuid TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS runs_project_time ON runs(project,created DESC);
                CREATE INDEX IF NOT EXISTS runs_time ON runs(created DESC);
                CREATE TABLE IF NOT EXISTS decisions (
                    run_id TEXT NOT NULL REFERENCES runs(id), finding_key TEXT NOT NULL,
                    signature TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated TEXT NOT NULL,
                    PRIMARY KEY(run_id,finding_key)
                );
                CREATE TABLE IF NOT EXISTS decision_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    finding_key TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS decision_run ON decision_events(run_id,id);
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS design_contexts (
                    project_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS context_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_key TEXT NOT NULL,
                    revision INTEGER NOT NULL, value TEXT NOT NULL, at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, project_key TEXT NOT NULL,
                    report_id TEXT NOT NULL, channel TEXT NOT NULL, request_id TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    prompt TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
                    UNIQUE(project_key,channel,request_id)
                );
                CREATE INDEX IF NOT EXISTS conversation_project_time ON project_conversations(project_key,id);
                CREATE INDEX IF NOT EXISTS context_event_project ON context_events(project_key,revision);
                CREATE TABLE IF NOT EXISTS meta_usage (
                    request_id TEXT PRIMARY KEY, at TEXT NOT NULL, model TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    input_usd_per_m REAL NOT NULL, output_usd_per_m REAL NOT NULL,
                    cached_usd_per_m REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS meta_usage_time ON meta_usage(at);
            ''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(project_conversations)')}
            if 'metadata' not in columns:
                db.execute("ALTER TABLE project_conversations ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            db.execute('PRAGMA user_version=4')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()

    def index(self, run_id, data, *, mtime_ns, created=None):
        project=data.get('provenance',{}).get('project_name') or Path(data['archive']).stem
        summary={'id':run_id,'name':data['archive'],'project':project,
                 'created':created or data.get('provenance',{}).get('created') or timestamp(),
                 'components':len(data.get('components',{})),
                 'ai_status':data.get('ai_review',{}).get('status','not_requested'),
                 'headline':data.get('review',{}).get('headline','Saved project report'),
                 'assembly_status':data.get('assembly',{}).get('status','unavailable'),
                 'review_groups':sum(g.get('priority') in ('fix','review') for g in data.get('review',{}).get('groups',[]))}
        with self.connection() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET project=excluded.project,created=excluded.created,summary=excluded.summary,mtime_ns=excluded.mtime_ns,source_uuid=excluded.source_uuid',
                (run_id,project,summary['created'],json.dumps(summary),mtime_ns,data.get('provenance',{}).get('schematic_uuid','')))

    def known(self):
        with self.connection() as db:
            return {r['id']:r['mtime_ns'] for r in db.execute('SELECT id,mtime_ns FROM runs')}

    def library(self, *, query='', project='', offset=0, limit=30, latest=False):
        if not isinstance(query,str) or not isinstance(project,str) or len(query)>200 or len(project)>120:
            raise ValueError('Invalid library filter.')
        if not isinstance(offset,int) or not 0<=offset<=1000000 or not isinstance(limit,int) or not 1<=limit<=100:
            raise ValueError('Invalid library page.')
        # Literal substring matching: user % and _ are not SQL wildcards.
        escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        where='WHERE (project LIKE ? ESCAPE \'\\\' OR id LIKE ? ESCAPE \'\\\')'
        params=['%'+escaped+'%']*2
        if project:where+=' AND project=?';params.append(project)
        with self.connection() as db:
            if latest:
                ranked=('WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY project,source_uuid ORDER BY created DESC,id DESC) AS newest, '
                        'COUNT(*) OVER (PARTITION BY project,source_uuid) AS versions FROM runs) ')
                total=db.execute(ranked+'SELECT COUNT(*) FROM ranked '+where+' AND newest=1',params).fetchone()[0]
                rows=db.execute(ranked+'SELECT summary,versions FROM ranked '+where+' AND newest=1 ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            else:
                total=db.execute('SELECT COUNT(*) FROM runs '+where,params).fetchone()[0]
                rows=db.execute('SELECT summary FROM runs '+where+' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            projects=[dict(r) for r in db.execute('SELECT project AS name,COUNT(*) AS runs FROM runs GROUP BY project ORDER BY project COLLATE NOCASE')]
        return {'items':[json.loads(r[0]) | ({'version_count':r['versions']} if latest else {}) for r in rows],
                'total':total,'offset':offset,'limit':limit,'projects':projects,'grouped':latest}

    def run(self, run_id):
        with self.connection() as db:
            row=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        return dict(row) if row else None

    def decisions(self,run_id):
        with self.connection() as db:
            return {r['finding_key']:dict(r) for r in db.execute('SELECT * FROM decisions WHERE run_id=?',(run_id,))}

    def events(self,run_id):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM decision_events WHERE run_id=? ORDER BY id',(run_id,))]

    def annotate(self,run_id,key,signature,status,note,expected_revision=0):
        if status not in ('open','reviewed','accepted_risk'):
            raise ValueError('Choose open, reviewed, or accepted risk.')
        if not isinstance(note,str) or len(note)>4000 or '\x00' in note:
            raise ValueError('Keep review notes under 4,000 characters.')
        if status!='open' and not note.strip():
            raise ValueError('Add a reason or review note before recording this decision.')
        if not isinstance(expected_revision,int) or expected_revision<0:
            raise ValueError('Invalid review revision.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision FROM decisions WHERE run_id=? AND finding_key=?',(run_id,key)).fetchone()
            revision=row[0] if row else 0
            if revision!=expected_revision:
                raise RevisionConflict('This decision changed in another tab. Refresh before saving your note.')
            at=timestamp()
            db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id,finding_key) DO UPDATE SET signature=excluded.signature,status=excluded.status,note=excluded.note,revision=excluded.revision,updated=excluded.updated',
                       (run_id,key,signature,status,note.strip(),revision+1,at))
            db.execute('INSERT INTO decision_events(run_id,finding_key,status,note,at) VALUES (?,?,?,?,?)',(run_id,key,status,note.strip(),at))
        return self.decisions(run_id)[key]

    def preference(self,key,value=None):
        # The poll loop reads a few small preferences on every refresh. Cache
        # the stored JSON so a transient storage outage cannot take down reads
        # of values this process already committed. Unknown keys still read
        # through so a failure stays a failure instead of invented data.
        if value is None:
            with self._preference_lock:
                if key in self._preference_cache:
                    payload=self._preference_cache[key]
                    return json.loads(payload) if payload is not None else None
            with self.connection() as db:
                row=db.execute('SELECT value FROM preferences WHERE key=?',(key,)).fetchone()
            payload=row[0] if row else None
            with self._preference_lock:
                self._preference_cache[key]=payload
            return json.loads(payload) if payload is not None else None
        payload=json.dumps(value)
        with self.connection() as db:
            db.execute('INSERT INTO preferences VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,payload))
            row=db.execute('SELECT value FROM preferences WHERE key=?',(key,)).fetchone()
        stored=row[0] if row else payload
        with self._preference_lock:
            self._preference_cache[key]=stored
        return json.loads(stored)

    def design_context(self, key):
        with self.connection() as db:
            row = db.execute('SELECT value FROM design_contexts WHERE project_key=?', (key,)).fetchone()
        value=json.loads(row[0]) if row else None
        if value is not None:
            value['explanation_preferences'] = self.preference('explanation:'+key) or {}
            messages=self.conversations(key,limit=20)
            memory=[];size=0
            for row in reversed(messages):
                item={k:row[k] for k in ('channel','question','prompt','at')}
                cost=len(json.dumps(item).encode())
                if size+cost>12000:continue
                memory.append(item);size+=cost
            if memory:value['conversation_memory']=list(reversed(memory))
        return value

    def conversations(self,key,channel=None,limit=100):
        with self.connection() as db:
            clause='project_key=?';args=[key]
            if channel is not None:clause+=' AND channel=?';args.append(channel)
            rows=db.execute('SELECT * FROM project_conversations WHERE '+clause+' ORDER BY id DESC LIMIT ?',args+[limit]).fetchall()
        result=[]
        for row in reversed(rows):
            item=dict(row)
            try:item['metadata']=json.loads(item.get('metadata') or '{}')
            except (ValueError,TypeError):item['metadata']={}
            result.append(item)
        return result

    def save_conversation(self,key,report_id,channel,request_id,question,prompt=''):
        learned=None
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted=db.execute('INSERT OR IGNORE INTO project_conversations(project_key,report_id,channel,request_id,question,status,prompt,at) VALUES (?,?,?,?,?,?,?,?)',
                (key,report_id,channel,request_id,question,'pending',prompt,timestamp()))
            if inserted.rowcount:
                from .explanation_profile import learn
                prefkey='explanation:'+key
                row=db.execute('SELECT value FROM preferences WHERE key=?',(prefkey,)).fetchone()
                before=json.loads(row[0]) if row else {}
                after=learn(before,question,prompt)
                if after!=before:
                    learned=(prefkey,json.dumps(after))
                    db.execute('INSERT INTO preferences VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',learned)
        if learned:
            with self._preference_lock:
                self._preference_cache[learned[0]]=learned[1]

    def finish_conversation(self,key,channel,request_id,result):
        metadata={k:result[k] for k in ('citations','web_searched','provider_label','evidence_state','proposals','decision_basis','decision_update') if k in result}
        with self.connection() as db:
            db.execute('UPDATE project_conversations SET answer=?,status=?,metadata=? WHERE project_key=? AND channel=? AND request_id=?',
                (result.get('answer') or result.get('message',''),result.get('status','failed'),json.dumps(metadata),key,channel,request_id))

    def reset_project(self, project_keys, report_ids):
        """Delete every stored row for the given context keys and report IDs.

        Used by demo-project reset. Spend accounting (meta_usage) is never
        touched: deleting reviews must not rewrite the usage history.
        Returns a dict of removed row counts per table.
        """
        keys, rids = set(project_keys), set(report_ids)
        removed = {}
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            pairs = [
                ('design_contexts', 'project_key', keys),
                ('context_events', 'project_key', keys),
                ('project_conversations', 'project_key', keys),
                ('decisions', 'run_id', rids),
                ('decision_events', 'run_id', rids),
                ('runs', 'id', rids),
            ]
            for table, column, values in pairs:
                if not values:
                    removed[table] = 0
                    continue
                placeholders = ','.join('?' * len(values))
                cursor = db.execute(f'DELETE FROM {table} WHERE {column} IN ({placeholders})',
                                    tuple(values))
                removed[table] = cursor.rowcount
            pref_keys = [f'input:{rid}' for rid in rids]
            for key in keys:
                pref_keys += [f'explanation:{key}', f'manufacturer:{key}',
                              f'verification:{key}']
            if pref_keys:
                placeholders = ','.join('?' * len(pref_keys))
                cursor = db.execute(f'DELETE FROM preferences WHERE key IN ({placeholders})',
                                    tuple(pref_keys))
                removed['preferences'] = cursor.rowcount
            else:
                removed['preferences'] = 0
            with self._preference_lock:
                for cached in pref_keys:
                    self._preference_cache.pop(cached, None)
        return removed

    def delete_preference(self, key):
        """Remove one preference row (used to unlink a reset demo project)."""
        with self.connection() as db:
            db.execute('DELETE FROM preferences WHERE key=?', (key,))
        with self._preference_lock:
            self._preference_cache.pop(key, None)

    def save_context(self, key, value, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid context revision.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM design_contexts WHERE project_key=?', (key,)).fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise RevisionConflict('Design context changed in another tab. Refresh before updating it.')
            saved = {k:v for k,v in value.items() if k not in ('conversation_memory','explanation_preferences')}
            saved.update(revision=revision+1,updated=timestamp())
            db.execute('INSERT INTO design_contexts VALUES (?,?,?) ON CONFLICT(project_key) DO UPDATE SET revision=excluded.revision,value=excluded.value',
                       (key, revision + 1, json.dumps(saved)))
            db.execute('INSERT INTO context_events(project_key,revision,value,at) VALUES (?,?,?,?)',
                (key,revision+1,json.dumps(saved),saved['updated']))
        return self.design_context(key)

    def record_meta_usage(self, record):
        """Persist one Meta request. Retried records share a request_id: no double counting."""
        if not isinstance(record, dict):
            raise ValueError('Invalid usage record.')
        request_id = record.get('request_id')
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64:
            raise ValueError('Invalid usage record.')
        if record.get('status') not in ('completed', 'incomplete', 'failed'):
            raise ValueError('Invalid usage record.')
        tokens = {}
        for field in ('input_tokens', 'output_tokens', 'cached_input_tokens'):
            value = record.get(field, 0)
            if type(value) is not int or value < 0:
                raise ValueError('Invalid usage record.')
            tokens[field] = value
        prices = {}
        for field in ('input_usd_per_m', 'output_usd_per_m', 'cached_usd_per_m'):
            value = record.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError('Invalid usage record.')
            prices[field] = float(value)
        model = record.get('model', '')
        action = record.get('action', '')
        if not isinstance(model, str) or len(model) > 110 or not isinstance(action, str) or len(action) > 40:
            raise ValueError('Invalid usage record.')
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO meta_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, record.get('at') or timestamp(), model, action, record['status'],
                        tokens['input_tokens'], tokens['output_tokens'], tokens['cached_input_tokens'],
                        prices['input_usd_per_m'], prices['output_usd_per_m'], prices['cached_usd_per_m']))

    def meta_usage_summary(self, *, budget_usd, warn_usd, warn_high_usd, model, pricing):
        """Billed requests only; failed attempts cost nothing and stay out of the counts."""
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        cost = ('COALESCE(SUM((MAX(input_tokens-cached_input_tokens,0)*input_usd_per_m'
                '+cached_input_tokens*cached_usd_per_m+output_tokens*output_usd_per_m)/1000000.0),0)')
        with self.connection() as db:
            row = db.execute(
                f'SELECT COUNT(*),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'
                f'COALESCE(SUM(cached_input_tokens),0),{cost} FROM meta_usage WHERE status!=?', ('failed',)).fetchone()
            today = db.execute(
                f'SELECT COUNT(*),{cost} FROM meta_usage WHERE status!=? AND at>=?', ('failed', midnight)).fetchone()
        total = float(row[4])
        return {'model': model, 'pricing': dict(pricing),
                'requests': row[0], 'input_tokens': row[1], 'output_tokens': row[2],
                'cached_input_tokens': row[3], 'total_tokens': row[1] + row[2],
                'estimated_cost_usd': total, 'today_requests': today[0],
                'today_cost_usd': float(today[1]), 'budget_usd': budget_usd,
                'warn_usd': warn_usd, 'warn_high_usd': warn_high_usd,
                'remaining_usd': budget_usd - total,
                'level': 'over' if total >= budget_usd else 'high' if total >= warn_high_usd
                         else 'warn' if total >= warn_usd else 'ok'}
