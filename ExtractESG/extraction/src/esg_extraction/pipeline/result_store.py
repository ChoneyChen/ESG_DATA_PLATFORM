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
        with self.connect() as db:
            indexed = db.execute("SELECT 1 FROM sqlite_master WHERE name='fact_terms'").fetchone()
            db.executescript("CREATE TABLE IF NOT EXISTS fact_terms (fact_id TEXT PRIMARY KEY, unit TEXT, text TEXT); CREATE INDEX IF NOT EXISTS result_units ON fact_terms(unit);")
            if not indexed:
                for row in db.execute("SELECT id FROM facts").fetchall():
                    fact = self.artifacts.read(self.run_id,f"facts/{row['id']}.json")
                    self._index_terms(db,fact)
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

    @staticmethod
    def _index_terms(db, fact):
        identity = fact.get('identity',{})
        fields = [v for k,v in identity.items() if k != 'dimensions'] + list(identity.get('dimensions',{}).values())
        text = ' '.join([fact.get('source_row_label') or '',fact['value'].get('value_raw') or fact['value'].get('statement_raw') or '',
            *[field.get('raw_text') or '' for field in fields]])
        db.execute('INSERT INTO fact_terms VALUES (?,?,?) ON CONFLICT(fact_id) DO UPDATE SET unit=excluded.unit,text=excluded.text',
            (fact['source_fact_id'],fact['value'].get('unit_raw'),text.casefold()))

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
                    self._index_terms(db,fact)
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

    def page(self, *, limit=100, cursor=None, clause_key=None, period=None, relation=None, unit=None, q=None, fact_id=None):
        import base64
        query = digest([self.run_id,limit,clause_key,period,relation,unit,q,fact_id])
        after = 0
        if cursor:
            token = json.loads(base64.urlsafe_b64decode(cursor+'='*(-len(cursor)%4)))
            if token['query'] != query:
                raise ValueError('Cursor belongs to another result query')
            after = token['after']
        terms,params = [],[]
        if fact_id:
            terms.append('f.id=?');params.append(fact_id)
        if unit:
            terms.append('EXISTS (SELECT 1 FROM fact_terms t WHERE t.fact_id=f.id AND t.unit=?)');params.append(unit)
        if q:
            terms.append('EXISTS (SELECT 1 FROM fact_terms t WHERE t.fact_id=f.id AND instr(t.text,?)>0)');params.append(q.casefold())
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

    def overview(self):
        with self.connect() as db:
            counts = [dict(row) for row in db.execute('SELECT clause,relation,COUNT(*) AS facts FROM relations GROUP BY clause,relation')]
            periods = {row['period']:row['facts'] for row in db.execute('SELECT period,COUNT(*) AS facts FROM facts GROUP BY period')}
        return {'revision':self.revision(),'clauses':counts,'periods':periods,**(self.status() or {})}

    def facets(self, *, clause_key):
        with self.connect() as db:
            periods = [row[0] for row in db.execute('SELECT DISTINCT f.period FROM facts f JOIN relations r ON r.fact_id=f.id WHERE r.clause=? ORDER BY f.period',(clause_key,))]
            units = [row[0] for row in db.execute("SELECT DISTINCT t.unit FROM fact_terms t JOIN relations r ON r.fact_id=t.fact_id WHERE r.clause=? AND t.unit IS NOT NULL AND t.unit<>'' ORDER BY t.unit",(clause_key,))]
            relations = {row[0]:row[1] for row in db.execute('SELECT relation,COUNT(*) FROM relations WHERE clause=? GROUP BY relation',(clause_key,))}
        return {'periods':periods,'units':units,'relations':relations}

    def frozen_table(self, clause_key, fact_ids):
        """Index an existing frozen view once; no historical clause is recompiled."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ready = db.execute("SELECT 1 FROM sqlite_master WHERE name='frozen_view_definitions'").fetchone()
            if not ready:
                cached = self.artifacts.read(self.run_id,'results/organized.json')
                db.execute('CREATE TABLE frozen_view_definitions (clause TEXT PRIMARY KEY, body TEXT)')
                db.execute('CREATE TABLE frozen_view_rows (clause TEXT, fact_id TEXT, body TEXT, PRIMARY KEY(clause,fact_id))')
                for table in cached['tables']:
                    definition = dict(table['definition'])
                    definition.pop('curation',None)
                    definition['schema_version'] = 'clause-organization-1.1'
                    definition['fields'] = [{key:value for key,value in field.items() if key != 'vocabulary'} for field in definition['fields']]
                    meta = {**table,'definition':definition,'rows':[],'groups':[],'matrix':None}
                    db.execute('INSERT INTO frozen_view_definitions VALUES (?,?)',(table['clause_key'],json.dumps(meta,ensure_ascii=False)))
                    db.executemany('INSERT INTO frozen_view_rows VALUES (?,?,?)',[(table['clause_key'],row['source_fact_id'],json.dumps(row,ensure_ascii=False)) for row in table['rows']])
            meta = json.loads(db.execute('SELECT body FROM frozen_view_definitions WHERE clause=?',(clause_key,)).fetchone()[0])
            rows = {row['fact_id']:json.loads(row['body']) for row in db.execute('SELECT fact_id,body FROM frozen_view_rows WHERE clause=? AND fact_id IN ('+','.join('?' for _ in fact_ids)+')',(clause_key,*fact_ids))} if fact_ids else {}
        return {**meta,'rows':[rows[fid] for fid in fact_ids if fid in rows]}
