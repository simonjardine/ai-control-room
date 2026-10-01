"""Turn a preset into reply steps, and tell each model what its step is for."""
from __future__ import annotations

from uuid import uuid4


def plan(order, invited, channel, rounds=1, blind_first=False, verdict=None):
    """Return reply steps in speaking order.

    Private chats are always one ordinary reply. In the public room each round lets every
    invited debater speak once; the verdict seat (if invited) speaks once, last.
    """
    invited = [f for f in order if f in invited]
    round_id = uuid4().hex
    if channel != 'public':
        return [dict(fid=f, channel=channel, job='answer', round=1, rounds=1, blind=False,
                     round_id=round_id) for f in invited]
    judge = verdict if verdict in invited else None
    debaters = [f for f in invited if f != judge]
    steps = []
    for number in range(1, rounds + 1):
        for f in debaters:
            steps.append(dict(fid=f, channel=channel, job='answer' if number == 1 else 'revise',
                              round=number, rounds=rounds, blind=blind_first and number == 1,
                              round_id=round_id))
    if judge:
        steps.append(dict(fid=judge, channel=channel, job='verdict', round=rounds, rounds=rounds,
                          blind=False, round_id=round_id))
    return steps


def round_key(step):
    """Entries sharing a key were written in the same round of the same run."""
    return f"{step['round_id']}:{step['round']}" if step.get('round_id') else None


def instruction(step):
    """Short per-step guidance appended to the seat's own prompt."""
    if not step or step.get('channel') != 'public':
        return ''
    job, number, rounds = step.get('job'), step.get('round', 1), step.get('rounds', 1)
    if job == 'verdict':
        return ('\nTHIS TURN: you give the final verdict on the discussion above. Do not add a new opinion '
                'of your own. State the recommended answer, the strongest supporting points, and any '
                'disagreements that remain. Under 200 words.')
    lines = []
    if rounds > 1:
        lines.append(f'This is round {number} of {rounds}.')
    if step.get('blind'):
        lines.append('Answer independently: the other participants are answering at the same time '
                     'and you cannot see their replies yet.')
    if job == 'revise':
        lines.append('Read the replies since your last turn. Revise or defend your answer. Name at least '
                     'one specific point of disagreement, or say plainly that you now have none. '
                     'Under 120 words.')
    return '\nTHIS TURN: ' + ' '.join(lines) if lines else ''
