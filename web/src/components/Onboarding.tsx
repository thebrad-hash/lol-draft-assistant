import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactElement,
  type ReactNode,
  type RefObject,
} from 'react';
import { createPortal } from 'react-dom';

// Dependency-free onboarding primitives, hextech-skinned (styles/onboarding.css):
//   Tooltip   — hover/focus hint for persistent vocabulary ("≈ tied", Auto-adapt).
//   Coachmark — dismissible first-run callout anchored over a target element.
// Dismissals + guide collapse persist in localStorage so hints show once; the
// lobby's "?" affordance (reopenHints) brings everything back.

/* -------------------------------- storage -------------------------------- */

const KEY = 'ld_onboarding_v1';

type OnboardingState = {
  dismissed: string[]; // coachmark ids already acknowledged
  guideCollapsed: boolean; // "How to use this" panel folded?
};

function load(): OnboardingState {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const p = JSON.parse(raw);
      return {
        dismissed: Array.isArray(p.dismissed)
          ? p.dismissed.filter((x: unknown): x is string => typeof x === 'string')
          : [],
        guideCollapsed: p.guideCollapsed === true,
      };
    }
  } catch {
    /* corrupt or blocked storage -> first-run defaults */
  }
  return { dismissed: [], guideCollapsed: false };
}

let state: OnboardingState = load();
const listeners = new Set<() => void>();

function setState(next: OnboardingState) {
  state = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode: hints reset per session, still functional */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

// Module-level actions (stable identities, safe in effect deps).
function dismiss(...ids: string[]) {
  setState({ ...state, dismissed: [...new Set([...state.dismissed, ...ids])] });
}
function setGuideCollapsed(v: boolean) {
  setState({ ...state, guideCollapsed: v });
}
function reopenHints() {
  setState({ dismissed: [], guideCollapsed: false });
}

export function useOnboarding() {
  const snap = useSyncExternalStore(subscribe, () => state, () => state);
  return {
    dismissed: snap.dismissed,
    guideCollapsed: snap.guideCollapsed,
    dismiss,
    setGuideCollapsed,
    reopenHints,
  };
}

/* ------------------------------ positioning ------------------------------ */

type Place = 'top' | 'bottom';
type Pos = { top: number; left: number; place: Place; caret: number };

const GAP = 10; // anchor <-> bubble distance (leaves room for the caret)
const PAD = 8; // min distance from viewport edges

function computePos(anchor: HTMLElement, float: HTMLElement, prefer: Place): Pos {
  const a = anchor.getBoundingClientRect();
  const f = float.getBoundingClientRect();
  let place: Place = prefer;
  let top = prefer === 'top' ? a.top - f.height - GAP : a.bottom + GAP;
  if (prefer === 'top' && top < PAD) {
    place = 'bottom';
    top = a.bottom + GAP;
  } else if (prefer === 'bottom' && top + f.height > window.innerHeight - PAD) {
    place = 'top';
    top = a.top - f.height - GAP;
  }
  const ideal = a.left + a.width / 2 - f.width / 2;
  const left = Math.min(Math.max(ideal, PAD), Math.max(window.innerWidth - f.width - PAD, PAD));
  // caret chases the anchor center but stays inside the bubble's frame
  const caret = Math.min(Math.max(a.left + a.width / 2 - left, 14), f.width - 14);
  return { top, left, place, caret };
}

/** Keeps a portal'd bubble glued to its anchor (recomputes on resize/scroll). */
function usePinned(
  anchorRef: RefObject<HTMLElement | null>,
  floatRef: RefObject<HTMLElement | null>,
  open: boolean,
  prefer: Place,
) {
  const [pos, setPos] = useState<Pos | null>(null);
  const update = useCallback(() => {
    if (anchorRef.current && floatRef.current) {
      setPos(computePos(anchorRef.current, floatRef.current, prefer));
    }
  }, [anchorRef, floatRef, prefer]);
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open, update]);
  return pos;
}

/* -------------------------------- Tooltip -------------------------------- */

export function Tooltip({
  label,
  children,
  place = 'top',
  focusable = false,
}: {
  label: ReactNode;
  children: ReactElement;
  place?: Place;
  /** set when the wrapped element isn't focusable itself (plain text/badge) */
  focusable?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const pos = usePinned(wrapRef, tipRef, open, place);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  // describedby lives on the real trigger (button etc.); the wrapper only
  // carries it when it is itself the focus stop.
  const child = isValidElement(children)
    ? cloneElement(children as ReactElement<Record<string, unknown>>, {
        'aria-describedby': open && !focusable ? id : undefined,
      })
    : children;

  return (
    <>
      <span
        ref={wrapRef}
        className="tip__wrap"
        tabIndex={focusable ? 0 : undefined}
        aria-describedby={focusable && open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {child}
      </span>
      {open &&
        createPortal(
          <div
            ref={tipRef}
            id={id}
            role="tooltip"
            className={'tip' + (pos ? ` tip--${pos.place}` : '')}
            style={{
              top: pos?.top ?? 0,
              left: pos?.left ?? 0,
              visibility: pos ? 'visible' : 'hidden',
            }}
          >
            {label}
            <span className="tip__caret" style={{ left: pos?.caret ?? '50%' }} aria-hidden />
          </div>,
          document.body,
        )}
    </>
  );
}

/* ------------------------------- Coachmark ------------------------------- */

// Esc dismisses the top-most open coachmark only (not all at once).
const coachStack: string[] = [];

export function Coachmark({
  id,
  title,
  children,
  anchorRef,
  place = 'top',
  onSkipAll,
}: {
  /** stable id persisted in localStorage once dismissed */
  id: string;
  title: string;
  children: ReactNode;
  /** the element this callout points at (rendered as a sibling, not a wrapper) */
  anchorRef: RefObject<HTMLElement | null>;
  place?: Place;
  /** "skip all hints" — wire to dismiss(...everyIdOnThisScreen) */
  onSkipAll?: () => void;
}) {
  const { dismissed } = useOnboarding();
  const open = !dismissed.includes(id);
  const htmlId = useId();
  const boxRef = useRef<HTMLDivElement>(null);
  const pos = usePinned(anchorRef, boxRef, open, place);

  useEffect(() => {
    if (!open) return;
    coachStack.push(id);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && coachStack[coachStack.length - 1] === id) dismiss(id);
    };
    document.addEventListener('keydown', onKey);
    return () => {
      const i = coachStack.lastIndexOf(id);
      if (i >= 0) coachStack.splice(i, 1);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, id]);

  // The anchor is "described by" the callout while it shows.
  useEffect(() => {
    const el = anchorRef.current;
    if (!el || !open) return;
    el.setAttribute('aria-describedby', htmlId);
    return () => el.removeAttribute('aria-describedby');
  }, [open, htmlId, anchorRef]);

  if (!open) return null;

  return createPortal(
    <div
      ref={boxRef}
      id={htmlId}
      className={'coach' + (pos ? ` coach--${pos.place}` : '')}
      style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? 'visible' : 'hidden' }}
    >
      <div className="coach__title">{title}</div>
      <div className="coach__body">{children}</div>
      <div className="coach__row">
        <button className="coach__btn" onClick={() => dismiss(id)}>
          Got it
        </button>
        {onSkipAll && (
          <button className="coach__btn coach__btn--ghost" onClick={onSkipAll}>
            Skip all hints
          </button>
        )}
      </div>
      <button className="coach__x" aria-label="Dismiss hint" onClick={() => dismiss(id)}>
        ×
      </button>
      <span className="coach__caret" style={{ left: pos?.caret ?? '50%' }} aria-hidden />
    </div>,
    document.body,
  );
}
