"""Numerical API probe; inputs contain experiments, never expected answers."""
import argparse
import json
import os
from pathlib import Path

os.environ['PYBAMM_DISABLE_TELEMETRY'] = 'true'
import numpy as np
import pybamm


def make_simulation(config, steps=None):
    if config['model'] == 'analytic':
        model = pybamm.BaseModel()
        model.summary_variables = []
        q = pybamm.Variable('q')
        z = pybamm.Variable('z')
        p = pybamm.InputParameter('capacity')
        k = pybamm.InputParameter('relaxation')
        current = pybamm.Parameter('Current function [A]')
        model.rhs = {q: -current / p, z: (q - z) / (k * p)}
        model.initial_conditions = {q: 4, z: 4}
        model.variables = {'q': q, 'z': z, 'clock-weighted z': pybamm.t * z,
                           'Battery voltage [V]': q, 'Current [A]': current}
        if config.get('product'):
            model.variables['product'] = current * p
        if config.get('algebraic'):
            v = pybamm.Variable('constraint')
            model.algebraic = {v: v - q * q - k * z}
            model.initial_conditions[v] = 16 + 4 * k
            model.variables['constraint'] = v
        parameters = pybamm.ParameterValues({
            'Current function [A]': 1, 'Nominal cell capacity [A.h]': 1,
            'Initial temperature [K]': 298.15, 'Ambient temperature [K]': 298.15})
    else:
        model = getattr(pybamm.lithium_ion, config['model'])()
        parameters = model.default_parameter_values
        parameters.update({k: '[input]' for k in config['parameters']})
    if steps is not None:
        groups = [tuple(steps)]
    elif config.get('cycles'):
        groups = [tuple(cycle) for cycle in config['cycles']]
    else:
        groups = [tuple(config['steps'])]
    experiment = pybamm.Experiment(groups, period=config['period'])
    return pybamm.Simulation(model, parameter_values=parameters, experiment=experiment,
        solver=pybamm.IDAKLUSolver(rtol=1e-11, atol=1e-11))


def solve(config, enabled=False, reuse=None):
    simulation = reuse or make_simulation(config)
    kwargs = {'inputs': config['parameters'], 'calculate_sensitivities': enabled,
              'calc_esoh': False}
    if enabled:
        kwargs['calculate_event_sensitivities'] = True
    else:
        # Independent forward references retain dense Hermite interpolation;
        # fixed-time perturbations must not differentiate a coarse output grid.
        kwargs['t_interp'] = np.array([])
    split = config.get('resume_after')
    if split:
        first = make_simulation(config, config['steps'][:split])
        history = first.solve(**kwargs)
        second = make_simulation(config, config['steps'][split:])
        solution = second.solve(starting_solution=history, **kwargs)
    else:
        solution = simulation.solve(**kwargs)
    return solution


def steps_of(solution):
    return [step for cycle in solution.cycles for step in cycle.steps]


def values(solution, config):
    steps = steps_of(solution)
    rows = {'end_times': [], 'end_values': [], 'phase_values': [], 'fixed_values': []}
    for index, step in enumerate(steps):
        rows['end_times'].append(float(step.t[-1]))
        rows['end_values'].append([float(step[v].entries[-1]) for v in config['variables']])
        relative = config['queries'][index]['elapsed']
        absolute = config['queries'][index]['absolute']
        rows['phase_values'].append([float(step[v](step.t[0] + relative)) for v in config['variables']])
        rows['fixed_values'].append([float(step[v](absolute)) for v in config['variables']])
    return rows


def derivatives(solution, config):
    rows = {'end_times': [], 'end_values': [], 'phase_values': [], 'fixed_values': []}
    names = sorted(config['parameters'])
    for index, step in enumerate(steps_of(solution)):
        rows['end_times'].append([float(step.time_sensitivities[p][-1, 0]) for p in names])
        endpoint = []; phase = []; fixed = []
        for name in config['variables']:
            variable = step[name]
            phase_sens = variable.phase_sensitivities
            fixed_sens = variable.sensitivities
            endpoint.append([float(phase_sens[p][-1, 0]) for p in names])
            phase.append([float(np.interp(step.t[0] + config['queries'][index]['elapsed'],
                              step.t, np.asarray(phase_sens[p]).ravel())) for p in names])
            fixed.append([float(np.interp(config['queries'][index]['absolute'], step.t,
                              np.asarray(fixed_sens[p]).ravel())) for p in names])
        rows['end_values'].append(endpoint)
        rows['phase_values'].append(phase)
        rows['fixed_values'].append(fixed)
    return rows


def _arrays(values):
    return {key: np.asarray(value).copy() for key, value in values.items()}


def _public_snapshot(solution, config):
    """Forward samples and both sensitivity families, copied off the object."""
    variables = config['variables']
    phase = {}
    fixed = {}
    forward = {}
    for variable in variables:
        processed = solution[variable]
        phase[variable] = _arrays(processed.phase_sensitivities)
        fixed[variable] = _arrays(processed.sensitivities)
        forward[variable] = np.asarray(processed.entries).reshape(-1).copy()
    return {
        'time': _arrays(solution.time_sensitivities),
        'phase': phase,
        'fixed': fixed,
        'forward': forward,
        't': np.asarray(solution.t).copy(),
        'termination': solution.termination,
    }


def _jsonify(value):
    if isinstance(value, dict):
        return {key: _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    return np.asarray(value).tolist()


def _owns(source, copied):
    """A copy keeps its own storage, including when that storage is read-only."""
    source_array = np.asarray(source)
    copied_array = np.asarray(copied)
    before = np.array(source_array, copy=True)
    if source_array.shape != copied_array.shape or np.shares_memory(source_array, copied_array):
        return False
    if copied_array.size and copied_array.flags.writeable:
        copied_array.flat[0] = copied_array.flat[0] + 1.0
    return bool(np.array_equal(before, np.asarray(source)))


def _alias_check(solution, config):
    """Every returned derivative array, named or stacked, belongs to the copy."""
    copied = solution.copy()
    owned = {'time': True, 'phase': True, 'fixed': True, 'forward': True}
    for key in solution.time_sensitivities:
        owned['time'] = owned['time'] and _owns(
            solution.time_sensitivities[key], copied.time_sensitivities[key])
    for variable in config['variables']:
        owned['forward'] = owned['forward'] and _owns(
            solution[variable].entries, copied[variable].entries)
        for key in solution[variable].phase_sensitivities:
            owned['phase'] = owned['phase'] and _owns(
                solution[variable].phase_sensitivities[key],
                copied[variable].phase_sensitivities[key])
        for key in solution[variable].sensitivities:
            owned['fixed'] = owned['fixed'] and _owns(
                solution[variable].sensitivities[key], copied[variable].sensitivities[key])
    return owned


def _named_columns(mapping, names):
    return {name: np.asarray(mapping[name]).tolist() for name in names}


def _flag_off(value):
    """A missing property and an empty one both report no event-aligned times."""
    try:
        return bool(value())
    except Exception:
        # The page fixes no error type; a refused property is not reported.
        return False


def _produced(solution, variable):
    """Event-aligned properties exist only when both opt-in flags were set."""
    time_on = _flag_off(lambda: solution.time_sensitivities)
    phase_on = _flag_off(lambda: solution[variable].phase_sensitivities)
    return {'time': bool(time_on), 'phase': bool(phase_on)}


def _cycle_record(cycle, config, names):
    return {
        't': np.asarray(cycle.t).tolist(),
        'termination': cycle.termination,
        'time_all': np.asarray(cycle.time_sensitivities['all']).tolist(),
        'time_named': _named_columns(cycle.time_sensitivities, names),
        'phase_all': {name: np.asarray(cycle[name].phase_sensitivities['all']).tolist() for name in config['variables']},
        'phase_named': {name: _named_columns(cycle[name].phase_sensitivities, names) for name in config['variables']},
        'fixed_all': {name: np.asarray(cycle[name].sensitivities['all']).tolist() for name in config['variables']},
        'fixed_named': {name: _named_columns(cycle[name].sensitivities, names) for name in config['variables']},
        'forward': {name: np.asarray(cycle[name].entries).reshape(-1).tolist() for name in config['variables']},
        'plain_dict': type(cycle.time_sensitivities) is dict and all(
            type(cycle[name].phase_sensitivities) is dict for name in config['variables']),
        'time_keys': list(cycle.time_sensitivities),
        'phase_keys': {name: list(cycle[name].phase_sensitivities) for name in config['variables']},
    }


def series(solution, config):
    names = sorted(config['parameters'])
    steps = steps_of(solution)
    payload = {
        'time_all': np.asarray(solution.time_sensitivities['all']).tolist(),
        'time_named': _named_columns(solution.time_sensitivities, names),
        'phase_all': {},
        'phase_named': {},
        'fixed_all': {},
        'fixed_named': {},
        'forward': {},
        'times': np.asarray(solution.t).tolist(),
        'step_times': [np.asarray(step.t).tolist() for step in steps],
        'terminations': [step.termination for step in steps],
        'solution_termination': solution.termination,
        'plain_dict': {
            'phase': all(type(solution[name].phase_sensitivities) is dict for name in config['variables']),
            'time': type(solution.time_sensitivities) is dict,
        },
        'time_keys': list(solution.time_sensitivities),
        'phase_keys': {name: list(solution[name].phase_sensitivities) for name in config['variables']},
        'cycles': [_cycle_record(cycle, config, names) for cycle in solution.cycles],
        'steps': [{
            'time_all': np.asarray(step.time_sensitivities['all']).tolist(),
            'time_named': _named_columns(step.time_sensitivities, names),
            'phase_all': {name: np.asarray(step[name].phase_sensitivities['all']).tolist() for name in config['variables']},
            'phase_named': {name: _named_columns(step[name].phase_sensitivities, names) for name in config['variables']},
            'fixed_all': {name: np.asarray(step[name].sensitivities['all']).tolist() for name in config['variables']},
            'fixed_named': {name: _named_columns(step[name].sensitivities, names) for name in config['variables']},
            'forward': {name: np.asarray(step[name].entries).reshape(-1).tolist() for name in config['variables']},
            'plain_time': type(step.time_sensitivities) is dict,
            'plain_phase': {name: type(step[name].phase_sensitivities) is dict for name in config['variables']},
            'time_keys': list(step.time_sensitivities),
            'phase_keys': {name: list(step[name].phase_sensitivities) for name in config['variables']},
            'time_named_shape': {name: list(np.shape(step.time_sensitivities[name])) for name in names},
            'phase_named_shape': {
                variable: {name: list(np.shape(step[variable].phase_sensitivities[name])) for name in names}
                for variable in config['variables']},
            'fixed_named_shape': {
                variable: {name: list(np.shape(step[variable].sensitivities[name])) for name in names}
                for variable in config['variables']},
        } for step in steps],
    }
    for name in config['variables']:
        payload['phase_all'][name] = np.asarray(solution[name].phase_sensitivities['all']).tolist()
        payload['phase_named'][name] = _named_columns(solution[name].phase_sensitivities, names)
        payload['fixed_all'][name] = np.asarray(solution[name].sensitivities['all']).tolist()
        payload['fixed_named'][name] = _named_columns(solution[name].sensitivities, names)
        payload['forward'][name] = np.asarray(solution[name].entries).reshape(-1).tolist()
    return payload


def _step_snapshot(step, config):
    return {
        'time': _arrays(step.time_sensitivities),
        'phase': {name: _arrays(step[name].phase_sensitivities) for name in config['variables']},
        'fixed': {name: _arrays(step[name].sensitivities) for name in config['variables']},
        'forward': {name: np.asarray(step[name].entries).reshape(-1).copy() for name in config['variables']},
        't': np.asarray(step.t).copy(),
        'termination': step.termination,
    }


def _plain_sensitivity_maps(solution, config):
    """Record the mapping class before serialization rebuilds a dict."""
    return {
        'time': type(solution.time_sensitivities) is dict,
        'phase': all(
            type(solution[name].phase_sensitivities) is dict
            for name in config['variables']),
    }


def metadata(solution, config):
    names = sorted(config['parameters'])
    original = {k: np.asarray(v).copy() for k, v in solution.time_sensitivities.items()}
    steps = steps_of(solution)
    steps_before = [_step_snapshot(step, config) for step in steps]
    held_before = _public_snapshot(solution, config)
    copy = solution.copy()
    first = solution.first_state
    last = solution.last_state
    left = steps[0].copy()
    left_before = _step_snapshot(left, config)
    combined = left
    for step in steps[1:]:
        combined = combined + step
    left_after = _step_snapshot(left, config)
    steps_after = [_step_snapshot(step, config) for step in steps]
    alias = _alias_check(solution, config)
    held_after = _public_snapshot(solution, config)
    def arrays(values):
        return {key: np.asarray(value).tolist() for key, value in values.items()}
    result = {'samples': len(solution.t), 'before': arrays(original),
              'copy': arrays(copy.time_sensitivities), 'first': arrays(first.time_sensitivities),
              'last': arrays(last.time_sensitivities), 'combined': arrays(combined.time_sensitivities),
              'after': arrays(solution.time_sensitivities), 'variables': {},
              'steps': [{'samples': len(s.t), 'time': arrays(s.time_sensitivities)} for s in steps],
              'cycles': [{'samples': len(c.t), 'time': arrays(c.time_sensitivities),
                          'end_time': float(c.t[-1]), 'termination': c.termination,
                          'fixed': {v: arrays(c[v].sensitivities) for v in config['variables']},
                          'phase_end': {
                              v: arrays({p: c[v].phase_sensitivities[p][-1:] for p in names})
                              for v in config['variables']}} for c in solution.cycles],
              'held': {
                  'before': _jsonify(held_before),
                  'after': _jsonify(held_after),
                  'copy': _jsonify(_public_snapshot(copy, config)),
                  'first': _jsonify(_public_snapshot(first, config)),
                  'last': _jsonify(_public_snapshot(last, config)),
                  'combined': _jsonify(_public_snapshot(combined, config)),
                  'steps_before': _jsonify(steps_before),
                  'steps_after': _jsonify(steps_after),
                  'left_before': _jsonify(left_before),
                  'left_after': _jsonify(left_after),
              },
              'alias_isolated': alias,
              'plain_dict': {
                  'source': _plain_sensitivity_maps(solution, config),
                  'copy': _plain_sensitivity_maps(copy, config),
                  'first': _plain_sensitivity_maps(first, config),
                  'last': _plain_sensitivity_maps(last, config),
                  'combined': _plain_sensitivity_maps(combined, config),
              }}
    for variable in config['variables']:
        full = solution[variable].phase_sensitivities
        result['variables'][variable] = {
            'all': np.asarray(full['all']).tolist(),
            'named': np.hstack([full[p] for p in names]).tolist(),
            'full_first': arrays({p: full[p][:1] for p in names}),
            'full_last': arrays({p: full[p][-1:] for p in names}),
            'first': arrays({p: first[variable].phase_sensitivities[p] for p in names}),
            'last': arrays({p: last[variable].phase_sensitivities[p] for p in names})}
    return result


def _legacy(config):
    sim = make_simulation(config)
    sol = sim.solve(inputs=config['parameters'], calculate_sensitivities=True, calc_esoh=False)
    result = values(sol, config)
    result['fixed_gradients'] = {
        name: np.asarray(sol[name].sensitivities['all'])[-1].tolist()
        for name in config['variables']}
    result['terminations'] = [step.termination for step in steps_of(sol)]
    result['produced'] = _produced(sol, config['variables'][0])
    result['fixed_all'] = {
        name: np.asarray(sol[name].sensitivities['all']).tolist() for name in config['variables']}
    if config.get('legacy_event'):
        result['event_aligned'] = _flag_off(lambda: sol.time_sensitivities)
    return result


def _plain(config):
    simulation = make_simulation(config)
    solution = simulation.solve(inputs=config['parameters'], calc_esoh=False, t_interp=np.array([]))
    steps = steps_of(solution)
    return {
        'end_times': [float(step.t[-1]) for step in steps],
        'end_values': [[float(step[name].entries[-1]) for name in config['variables']] for step in steps],
        'times': np.asarray(solution.t).tolist(),
        'forward': {
            name: np.asarray(solution[name].entries).reshape(-1).tolist() for name in config['variables']},
        'terminations': [step.termination for step in steps],
        'produced': _produced(solution, config['variables'][0]),
    }


def _event_only(config):
    simulation = make_simulation(config)
    try:
        solution = simulation.solve(
            inputs=config['parameters'], calculate_sensitivities=False,
            calculate_event_sensitivities=True, calc_esoh=False)
    except Exception:
        # The page fixes no error type; a refused solve produces nothing.
        return {'produced': {'time': False, 'phase': False}}
    return {'produced': _produced(solution, config['variables'][0])}


def _terminating(config):
    solution = solve(config, enabled=True)
    step, rest = steps_of(solution)
    # The combined solution is what can see the following step's expression.
    endpoint = len(step.t) - 1
    return {
        'time': float(step.t[-1]),
        'output': float(np.asarray(solution['product'].entries).reshape(-1)[endpoint]),
        'phase': np.asarray(solution['product'].phase_sensitivities['all'])[endpoint].tolist(),
        'rest_output': float(rest['product'].entries[-1]),
        'names': list(step.all_models[-1].calculate_sensitivities),
    }


def run(config):
    mode = config.get('probe_mode')
    if mode == 'terminating':
        return _terminating(config)
    if mode == 'plain':
        return _plain(config)
    if mode == 'event_only':
        return _event_only(config)
    if mode == 'legacy':
        return _legacy(dict(config, legacy_event=True))
    if config.get('compatibility') or config.get('legacy_event'):
        return _legacy(config)
    sim = make_simulation(config)
    if config.get('reuse_parameters'):
        other = dict(config, parameters=config['reuse_parameters'])
        solve(other, enabled=True, reuse=sim)
    sol = solve(config, enabled=True, reuse=sim)
    result = {
        'values': values(sol, config),
        'derivatives': derivatives(sol, config),
        'series': series(sol, config),
    }
    try:
        result['metadata'] = metadata(sol, config)
    except Exception as error:
        result['metadata'] = {'error': type(error).__name__ + ': ' + str(error)}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(json.loads(args.input.read_text()))
    args.output.write_text(json.dumps(result, allow_nan=False))
