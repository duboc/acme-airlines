import { formatAsOf, Panel } from './Panel';
import { BudgetBody } from './panels/BudgetPanel';
import { ModelsPanel } from './panels/ModelsPanel';
import { SpendBody } from './panels/SpendPanel';
import { BILLING_EMPTY } from './format';
import { useApi } from './useApi';

const MODE = import.meta.env.VITE_DATA_MODE as string | undefined;

export default function App() {
  const spend = useApi('spend');
  const agents = useApi('agents');
  const github = useApi('github');
  const budget = useApi('budget');
  const asOfs = [spend, agents, github, budget]
    .map((r) => (r.status === 'done' ? r.data.as_of : null))
    .filter((a): a is string => a !== null)
    .sort();
  const newest = asOfs.at(-1) ?? null;
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to panels
      </a>
      <header className="app-header">
        <h1>ACME Observatory</h1>
        <p className="header-meta">
          {MODE === 'fake' && <span className="badge">Fake data</span>}
          <span>Latest data {formatAsOf(newest)}</span>
        </p>
      </header>
      <main id="main" className="grid">
        <Panel id="spend" title="Spend" result={spend} emptyMessage={BILLING_EMPTY}>
          {(d) => <SpendBody data={d} />}
        </Panel>
        <ModelsPanel />
        <Panel id="agents" title="Agents" result={agents} />
        <Panel id="github" title="GitHub" result={github} />
        <Panel id="budget" title="Budget" result={budget} emptyMessage={BILLING_EMPTY}>
          {(d) => <BudgetBody data={d} />}
        </Panel>
      </main>
    </>
  );
}
