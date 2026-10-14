import { ScrollRegion } from '../ScrollRegion';
import type { AgentsResponse } from '../api';
import { ago, duration } from '../format';

export const LEAD_STALE_SECONDS = 30 * 60;

export function AgentsBody({ data }: { data: AgentsResponse }) {
  const now = data.as_of;
  const idle =
    data.lead_last_activity && now
      ? (Date.parse(now) - Date.parse(data.lead_last_activity)) / 1000
      : null;
  const stale = idle === null || idle > LEAD_STALE_SECONDS;
  const roles = [...new Set(data.agents.map((a) => a.role))].sort();
  return (
    <>
      <p className={stale ? 'threshold threshold-warn' : 'threshold'}>
        <span aria-hidden="true">{stale ? '▲ ' : '● '}</span>
        {idle === null
          ? 'Stale: lead activity unknown'
          : `${stale ? 'Stale: lead' : 'Lead'} last active ${ago(data.lead_last_activity, now)}`}
      </p>
      {data.limits_exceeded_count > 0 && (
        <p>{data.limits_exceeded_count} agent(s) flagged LIMITS_EXCEEDED</p>
      )}
      <ScrollRegion label="Agents table, scrollable">
        <table>
          <caption className="sr-only">Agents grouped by role</caption>
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col">Tier</th>
              <th scope="col">Phase</th>
              <th scope="col">Activity</th>
              <th scope="col">Running</th>
              <th scope="col">Last activity</th>
            </tr>
          </thead>
          {roles.map((role) => (
            <tbody key={role}>
              <tr>
                <th scope="rowgroup" colSpan={6} className="group">
                  {role}
                </th>
              </tr>
              {data.agents
                .filter((a) => a.role === role)
                .map((a) => (
                  <tr key={a.name}>
                    <th scope="row">
                      {a.name}
                      {a.limits_exceeded && <strong className="flag"> ▲ LIMITS_EXCEEDED</strong>}
                    </th>
                    <td>{a.tier}</td>
                    <td>{a.phase}</td>
                    <td>{a.activity}</td>
                    <td>{duration(a.running_seconds)}</td>
                    <td>{ago(a.last_activity, now)}</td>
                  </tr>
                ))}
            </tbody>
          ))}
        </table>
      </ScrollRegion>
    </>
  );
}
