import { useEffect, useState } from 'react';
import { fetchApi, type Endpoints } from './api';

export type Result<T> =
  { status: 'loading' } | { status: 'error'; error: string } | { status: 'done'; data: T };

export function useApi<K extends keyof Endpoints>(name: K): Result<Endpoints[K]> {
  const [result, setResult] = useState<Result<Endpoints[K]>>({ status: 'loading' });
  useEffect(() => {
    const ctl = new AbortController();
    fetchApi(name, ctl.signal).then(
      (data) => setResult({ status: 'done', data }),
      (e: unknown) => {
        if (!ctl.signal.aborted)
          setResult({ status: 'error', error: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => ctl.abort();
  }, [name]);
  return result;
}
