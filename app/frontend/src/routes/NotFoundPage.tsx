import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="page">
      <h1>Page not found</h1>
      <p>
        This address does not exist. Go to the <Link to="/console">operations console</Link>.
      </p>
    </section>
  );
}
