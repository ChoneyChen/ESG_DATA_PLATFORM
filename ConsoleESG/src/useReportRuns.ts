import {useMemo} from 'react';
import {credentials,items} from './api';
import {useResource} from './state';
import {useLibraryResource} from './library-resource';
import {buildReportRuns} from './reportRun';

/**
 * Every page reads the same projection from the same three authorities:
 * the document service owns identity, evidence and extraction; the publication
 * service owns review and publication. No page composes its own state again.
 */
export function useReportRuns(){
 const library=useLibraryResource('report');
 const sessions=useResource('document','/api/report-sessions?include_archived=true&view=summary');
 const publications=useResource('publication',credentials.ready?'/internal/v1/report-publications':null,true);
 const status=useLibraryResource('status');
 const tasks=useMemo(()=>[...items(status.data,'active_tasks'),...items(status.data,'recent_tasks')],[status.data]);
 const projection=useMemo(()=>buildReportRuns({assets:items(library.data),sessions:items(sessions.data),
  publications:items(publications.data),tasks}),[library.data,sessions.data,publications.data,tasks]);
 return {library,sessions,publications,status,tasks,...projection};
}
