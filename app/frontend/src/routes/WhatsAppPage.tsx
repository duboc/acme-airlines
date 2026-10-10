import { ChatView } from '../whatsapp/ChatView';

// Mariana's passenger id in the seeded AC1234 scenario.
const PASSENGER_ID = 'P-MARIANA';

export function WhatsAppPage() {
  return (
    <section className="page">
      <h1>Passenger chat</h1>
      <p>This is a simulated WhatsApp conversation. No real messages are sent.</p>
      <ChatView passengerId={PASSENGER_ID} />
    </section>
  );
}
