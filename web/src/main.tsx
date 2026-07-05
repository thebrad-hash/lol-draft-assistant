import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './styles/tokens.css'; // design system first, so vars resolve everywhere
import './index.css';
import './styles/champ-select.css'; // hextech skin: overrides index.css structure
import './styles/confidence-bar.css'; // shared uncertainty bar (before card, which sizes it)
import './styles/recommendation-card.css';
import './styles/onboarding.css'; // tooltips, coachmarks, lobby guide

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
