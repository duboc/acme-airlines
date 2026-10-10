// Mirrors PresetView / ScenarioState in app/backend/openapi.json.
export interface PresetView {
  id: string;
  title: string;
  description: string;
  steps: string[];
  expectation: string;
}
export interface ScenarioState {
  scenario_id: string;
  step_index: number;
  total_steps: number;
  done: boolean;
  last_step: string | null;
  next_step: string | null;
  journey_id: string | null;
  journey_status: string | null;
  flight: string | null;
  seat: string | null;
  seat_reason: string | null;
  offers_sent: number;
  offer_status: string | null;
  charged: string;
  needs_human: boolean;
  last_message: string | null;
}
