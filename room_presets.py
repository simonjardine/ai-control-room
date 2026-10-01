"""Room presets: a role prompt for every seat plus how a public round is run."""
from __future__ import annotations

import copy

from room import DEFAULT_PROMPT, SEATS

FIDS = list(SEATS)
MY_PROMPTS = 'My prompts'
MAX_ROUNDS = 5


def _roles(**roles):
    return {f: DEFAULT_PROMPT + ('\nYOUR ROLE: ' + roles[f] if roles.get(f) else '') for f in FIDS}


BUILTIN = {
    'Best answer': dict(
        prompts=_roles(
            atlas='Be concise. Give the shortest correct, complete answer and nothing else.',
            jade="Devil's advocate. Look for what the others got wrong or overlooked, and say so plainly.",
            meridian='Fact-checker. Verify factual claims, using web tools when they are enabled, and flag anything unverified.',
            frontier='Alternatives. Offer approaches or answers the others have not considered.',
            haven='Practical lens. Focus on cost, effort, time and real-world trade-offs.',
            horizon='Final verdict. Weigh the discussion and give the recommended answer.'),
        rounds=3, blind_first=True, verdict='horizon'),
    'Pro vs con': dict(
        prompts=_roles(
            atlas='Argue FOR the proposition. Make the strongest honest case for it.',
            jade='Argue FOR the proposition. Add new supporting points rather than repeating others.',
            meridian='Argue FOR the proposition. Rebut the strongest point made against it.',
            frontier='Argue AGAINST the proposition. Make the strongest honest case against it.',
            haven='Argue AGAINST the proposition. Rebut the strongest point made for it.',
            horizon='Judge. Score both sides on evidence and reasoning, then say which case was stronger and why.'),
        rounds=3, blind_first=False, verdict='horizon'),
    'Stress-test my idea': dict(
        prompts=_roles(
            atlas="Critic. Find the weakest assumptions in the host's idea.",
            jade='Risk analyst. Identify what could go wrong, how likely it is and how bad it would be.',
            meridian='Fixer. For each problem raised, propose a concrete fix.',
            frontier="User's advocate. Judge the idea from the point of view of the people who would use it.",
            haven='Simplifier. Suggest what could be removed or made simpler without losing the point.',
            horizon='Rewriter. Produce an improved version of the idea that addresses the discussion.'),
        rounds=2, blind_first=False, verdict='horizon'),
    'Independent answers': dict(prompts=_roles(), rounds=1, blind_first=True, verdict=None),
    'Free discussion': dict(prompts=_roles(), rounds=3, blind_first=False, verdict=None),
}


def normalize(preset, fallback_prompts=None):
    """Return a complete, bounded preset; unknown or missing values fall back safely."""
    preset = preset if isinstance(preset, dict) else {}
    prompts = preset.get('prompts') if isinstance(preset.get('prompts'), dict) else {}
    fallback = fallback_prompts or {}
    rounds = preset.get('rounds', 1)
    rounds = min(MAX_ROUNDS, max(1, rounds)) if isinstance(rounds, int) and not isinstance(rounds, bool) else 1
    verdict = preset.get('verdict') if preset.get('verdict') in FIDS else None
    return dict(
        prompts={f: (prompts.get(f) if isinstance(prompts.get(f), str) and prompts.get(f).strip()
                     else fallback.get(f) or DEFAULT_PROMPT) for f in FIDS},
        rounds=rounds, blind_first=preset.get('blind_first') is True, verdict=verdict)


def load(settings):
    """All presets by name: saved ones override built-ins; "My prompts" always exists."""
    saved = settings.get('presets') if isinstance(settings.get('presets'), dict) else {}
    legacy = settings.get('prompts') if isinstance(settings.get('prompts'), dict) else {}
    presets = {MY_PROMPTS: normalize(saved.get(MY_PROMPTS) or dict(prompts=legacy), legacy)}
    for name, value in BUILTIN.items():
        presets[name] = normalize(saved.get(name, value))
    for name, value in saved.items():
        if isinstance(name, str) and name.strip() and name not in presets:
            presets[name] = normalize(value)
    return presets


def active_name(settings, presets):
    name = settings.get('active_preset')
    return name if name in presets else MY_PROMPTS


def is_builtin(name):
    return name in BUILTIN


def builtin_default(name):
    return normalize(copy.deepcopy(BUILTIN[name])) if name in BUILTIN else None


def describe(preset):
    rounds = preset['rounds']
    parts = ['1 round' if rounds == 1 else f'{rounds} rounds']
    if preset['blind_first']:
        parts.append('blind start')
    if preset['verdict']:
        parts.append('verdict')
    return ' · '.join(parts)
