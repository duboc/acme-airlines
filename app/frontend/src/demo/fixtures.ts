import type { PresetView, ScenarioState } from './types';

export const presets: PresetView[] = [
  {
    id: 'happy-path',
    title: 'Happy path',
    description: 'Scenarios 1-5 end to end.',
    steps: ['OCC cancels AC1234', 'Time passes', "Mariana: 'Can I leave after 10am?'"],
    expectation: 'Journey CLOSED on AC1252, seat 2A, $135.00 charged once.',
  },
  {
    id: 'seat-taken',
    title: 'Seat 14A taken',
    description: '14A is gone on AC1240.',
    steps: ['OCC cancels AC1234', 'Time passes'],
    expectation: 'Nearest window seat 15A, with the reason recorded.',
  },
];

export const base: ScenarioState = {
  scenario_id: 'happy-path',
  step_index: 0,
  total_steps: 3,
  done: false,
  last_step: null,
  next_step: 'OCC cancels AC1234',
  journey_id: null,
  journey_status: null,
  flight: null,
  seat: null,
  seat_reason: null,
  offers_sent: 0,
  offer_status: null,
  charged: '0.00',
  needs_human: false,
  last_message: null,
};
