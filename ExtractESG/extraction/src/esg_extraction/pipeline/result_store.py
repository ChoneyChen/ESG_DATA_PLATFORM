"""Producer-owned facts with a lightweight index; only changed records are written."""
import json
import sqlite3
from esg_data.hashing import digest
from esg_data.periods import period_label


class ResultStore:
    def __init__(self, run_id, artifacts):
        self.run_id, self.artifacts = run_id, artifacts
        self.path = artifacts.path(run_id, 'results/index.sqlite3')
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.pending = {}
        if fresh:
            with self.connect() as db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS facts (id TEXT PRIMARY KEY, period TEXT, kind TEXT, page INTEGER, digest TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS relations (fact_id TEXT, clause TEXT, relation TEXT, digest TEXT, PRIMARY KEY(fact_id,clause));
                    CREATE INDEX IF NOT EXISTS facts_period ON facts(period);
                    CREATE INDEX IF NOT EXISTS relation_filter ON relations(clause,relation);
                    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER);
                    INSERT OR IGNORE INTO metadata VALUES ('revision',0);
                ''')
        if fresh:
            old = artifacts.path(run_id, 'results/facts.json')
            if old.exists():
                body = json.loads(old.read_text())
                mappings = {}
                for link in body['clause_mappings']:
                    mappings.setdefault(link['source_fact_id'], []).append(link)
                for fact in body['facts']:
                    self.put(fact, mappings.get(fact['source_fact_id'], []), existing=True)
                self.flush()
                self.status({k:v for k,v in body.items() if k not in ('facts','clause_mappings')})

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def put(self, fact, mapping, *, existing=False):
        body = fact.model_dump(mode='json', exclude_none=True) if hasattr(fact,'model_dump') else fact
        links = mapping if isinstance(mapping,list) else [mapping]
        links = [m.model_dump(mode='json',exclude_none=True) if hasattr(m,'model_dump') else m for m in links]
        previous = self.pending.get(body['source_fact_id'])
        if previous:
            by_clause = {m['clause_key']:m for m in previous[1] + links}
            links = list(by_clause.values())
        self.pending[body['source_fact_id']] = (body,links,existing)

    def flush(self):
        if not self.pending:
            return
        changed = False
        with self.connect() as db:
            for fid,(fact,links,existing) in self.pending.items():
                signature = digest(fact)
                previous = db.execute('SELECT digest FROM facts WHERE id=?',(fid,)).fetchone()
                if previous is None or previous['digest'] != signature:
                    if not existing:
                        self.artifacts.write(self.run_id,f'facts/{fid}.json',fact)
                    identity = fact.get('identity',{}).get('reporting_period',{})
                    pages = [e['page_index'] for e in fact['evidence'] if e.get('page_index') is not None]
                    db.execute('INSERT INTO facts VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET period=excluded.period,kind=excluded.kind,page=excluded.page,digest=excluded.digest',
                        (fid,period_label(identity.get('value')),fact['value']['kind'],min(pages) if pages else None,signature))
                    changed = True
                for link in links:
                    key = link['clause_key']; signature = digest(link)
                    previous = db.execute('SELECT digest FROM relations WHERE fact_id=? AND clause=?',(fid,key)).fetchone()
                    if previous is None or previous['digest'] != signature:
                        self.artifacts.write(self.run_id,f'relations/{key}/{fid}.json',link)
                        db.execute('INSERT INTO relations VALUES (?,?,?,?) ON CONFLICT(fact_id,clause) DO UPDATE SET relation=excluded.relation,digest=excluded.digest',
                            (fid,key,link['match'],signature))
                        changed = True
            if changed:
                db.execute("UPDATE metadata SET value=value+1 WHERE key='revision'")
        self.pending.clear()

    def revision(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()[0]

    def status(self, body=None):
        if body is not None:
            self.artifacts.write(self.run_id,'results/status.json',body)
            return body
        try:
            return self.artifacts.read(self.run_id,'results/status.json')
        except FileNotFoundError:
            return None

    def checkpoint(self, body=None):
        if body is not None:
            self.artifacts.write(self.run_id,'results/checkpoint.json',body)
            return body
        try:
            return self.artifacts.read(self.run_id,'results/checkpoint.json')
        except FileNotFoundError:
            return {}

    def page(self, *, limit=100, cursor=None, clause_key=None, period=None, relation=None):
        import base64
        query = digest([self.run_id,limit,clause_key,period,relation])
        after = 0
        if cursor:
            token = json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            if token['query'] != query:
                raise ValueError('Cursor belongs to another result query')
            after = token['after']
        terms,params = [],[]
        if period:
            terms.append('f.period=?');params.append(period)
        if clause_key or relation:
            part = ['r.fact_id=f.id']
            for field,value in [('clause',clause_key),('relation',relation)]:
                if value is not None:
                    part.append('r.'+field+'=?');params.append(value)
            terms.append('EXISTS (SELECT 1 FROM relations r WHERE '+' AND '.join(part)+')')
        where = ' AND '.join(terms) if terms else '1'
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM facts f WHERE '+where,params).fetchone()[0]
            records = db.execute('SELECT f.id,f.rowid AS position FROM facts f WHERE '+where+' AND f.rowid>? ORDER BY f.rowid LIMIT ?',(*params,after,limit+1)).fetchall()
            selected = records[:limit]
            links = []
            for row in selected:
                for link in db.execute('SELECT clause FROM relations WHERE fact_id=?'+(' AND clause=?' if clause_key else ''),(row['id'],clause_key) if clause_key else (row['id'],)):
                    links.append(self.artifacts.read(self.run_id,f"relations/{link['clause']}/{row['id']}.json"))
        following = base64.urlsafe_b64encode(json.dumps({'query':query,'after':selected[-1]['position']}).encode()).decode().rstrip('=') if len(records)>limit else None
        return {'total':total,'returned':len(selected),'next_cursor':following,'query_digest':query,'revision':self.revision(),
            'facts':[self.artifacts.read(self.run_id,f"facts/{row['id']}.json") for row in selected], 'clause_mappings':links}

    def collection(self, *, clause_key=None):
        result = {'facts':[],'clause_mappings':[]};cursor=None
        while True:
            page = self.page(limit=500,cursor=cursor,clause_key=clause_key)
            for key in result:result[key].extend(page[key])
            cursor = page['next_cursor']
            if not cursor:return result

    def bundle(self):
        status = self.status()
        return {**status,**self.collection()} if status else None

    def overview(self):
        with self.connect() as db:
            counts = [dict(row) for row in db.execute('SELECT clause,relation,COUNT(*) AS facts FROM relations GROUP BY clause,relation')]
            periods = {row['period']:row['facts'] for row in db.execute('SELECT period,COUNT(*) AS facts FROM facts GROUP BY period')}
        return {'revision':self.revision(),'clauses':counts,'periods':periods,**(self.status() or {})}
