"""Read candidates as pages arrive; broaden only when the target has not been found."""


class CandidateStream:
    def __init__(self, documents, sources, intent, maximum, broaden):
        self.documents, self.sources = documents, list(sources)
        self.intent, self.maximum, self.broaden = intent, maximum, broaden
        self.seen = set()
        self.observation = {'total':None,'returned':0,'truncated':False,'queries':[],
            'scope':{'sources':{source.snapshot_id:source.search_scope or {} for source in self.sources}}}

    def __len__(self):
        return len(self.seen)

    def __iter__(self):
        try:
            yield from self._pages()
        except (OSError,ValueError) as exc:
            self.observation.update(truncated=True,error=f'{type(exc).__name__}: {str(exc)[:300]}')

    def _pages(self):
        for source in self.sources:
            for level in ('primary','broader'):
                if level == 'broader' and (not self.broaden() or not self.intent.required_term_groups):
                    break
                intent = self.intent.model_copy(update={'cursor':None,'source_snapshot':source.snapshot_id,
                    'required_term_groups':self.intent.required_term_groups if level == 'primary' else []})
                query = {'source_snapshot':source.snapshot_id,'level':level,'returned':0}
                self.observation['queries'].append(query)
                while True:
                    page = self.documents.search_page(source.ir_run_id,intent)
                    query.update(total=page.total,query_digest=page.query_digest)
                    for ref in page.objects:
                        identity = (source.snapshot_id,ref.object_id)
                        if identity in self.seen:
                            continue
                        if len(self.seen) >= self.maximum:
                            self.observation['truncated'] = True
                            return
                        self.seen.add(identity)
                        self.observation['returned'] = len(self.seen)
                        query['returned'] += 1
                        yield ref.model_copy(update={'source_snapshot':source.snapshot_id})
                    if not page.next_cursor:
                        break
                    intent = intent.model_copy(update={'cursor':page.next_cursor})
        self.observation['total'] = len(self.seen)


def retrieve_candidates(documents, ir_run_id, intent, maximum, *, sources=None, broaden=lambda: False):
    stream = CandidateStream(documents,sources or [documents.document(ir_run_id)],intent,maximum,broaden)
    return stream,stream.observation
