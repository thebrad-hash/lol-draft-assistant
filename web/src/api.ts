// ---------------------------------------------------------------------------
// THE SWAP POINT. The whole app imports `getRecommendations` from here.
//
// Today it points at the local mock. When the real backend (the Python EV
// engine exposed over HTTP) is ready, replace the import below with a real
// implementation that satisfies the same `GetRecommendations` signature, e.g.:
//
//   export const getRecommendations: GetRecommendations = async (state) => {
//     const res = await fetch('/api/recommend', {
//       method: 'POST',
//       headers: { 'content-type': 'application/json' },
//       body: JSON.stringify(state),
//     });
//     return res.json();
//   };
//
// Note: the real engine uses role keys ADC/SUP and weight keys
// in_lane/out_of_lane/...; map BOT->ADC, SUPPORT->SUP and camel->snake at that
// boundary. Nothing else in the UI needs to change.
// ---------------------------------------------------------------------------
// LIVE: talks to the Python FastAPI engine (falls back to mock if it's down).
export { getRecommendations } from './realApi';

// To force the offline mock instead, swap the line above for:
// export { getRecommendations } from './mock/api';
