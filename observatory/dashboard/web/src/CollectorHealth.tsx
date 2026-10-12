import { useApi } from './useApi';

/** Self-monitoring: the dashboard must never show "empty" silently when its own pipeline is down. */
export function CollectorHealth() {
  const r = useApi('collector-health');
  if (r.status === 'loading') return null;
  if (r.status === 'error') {
    return (
      <p className="health health-red" role="alert">
        ■ Collector health unknown ({r.error})
      </p>
    );
  }
  const d = r.data;
  if (d.healthy) {
    return <p className="health health-ok">● Collector healthy</p>;
  }
  const failed = d.last_execution?.status === 'failed' ? ' The last execution failed.' : '';
  return (
    <p className="health health-red" role="alert">
      ■ Collector down: {d.message ?? 'no recent snapshot.'}
      {d.message?.includes('failed') ? '' : failed}
    </p>
  );
}
