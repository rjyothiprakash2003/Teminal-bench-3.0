from pathlib import Path

import event_verify as ev

SUBMISSION = Path("/app/submission")


def test_submitted_package_is_complete():
    """The verifier imports only /app/submission/pybamm, with bundled input bytes."""
    ev.submitted_package_present()
    data = SUBMISSION / "pybamm" / "input"
    assert data.is_dir(), "keep bundled parameter data under /app/submission/pybamm"


def test_analytic_ode_event_derivatives():
    """Analytic event-terminated ODE derivatives match the sealed forward checks.

    The discharge endpoint of current times capacity stays on that step.
    The following rest expression is zero, so it cannot supply this derivative.
    """
    ev.check_forward_and_derivatives("analytic")
    ev.check_terminating_expression(False, 2.3, 2.7, 1.15, 2.3, [1.0, 0.0])


def test_analytic_dae_event_derivatives():
    """Analytic index-one DAE event derivatives stay within the contract tolerances.

    The same terminating-side output is required when the model has an algebraic constraint.
    """
    ev.check_forward_and_derivatives("analytic-dae")
    ev.check_terminating_expression(True, 2.3, 2.7, 1.15, 2.3, [1.0, 0.0])


def test_prescribed_duration_holds_elapsed_time_fixed():
    """A prescribed-duration endpoint holds that duration fixed in the derivatives."""
    ev.check_forward_and_derivatives("duration")


def test_spm_discharge_to_relaxation_derivatives():
    """SPM discharge-to-relaxation carries event-aligned parameter derivatives."""
    ev.check_forward_and_derivatives("battery")
    ev.check_flag_off("battery")
    ev.check_flag_off("battery-spme")


def test_spme_discharge_to_relaxation_derivatives():
    """SPMe discharge-to-relaxation carries event-aligned parameter derivatives.

    The implicit voltage hold is the same cut-off the constant-voltage case requires.
    """
    ev.check_forward_and_derivatives("battery-spme")
    ev.check_forward_and_derivatives("voltage-spme")
    ev.check_flag_off("battery-spme")
    ev.check_flag_off("voltage-control")
    ev.check_event_only("battery-spme")


def test_implicit_constant_voltage_termination():
    """Implicit constant-voltage termination and later rest keep event derivatives.

    The same hold is required on SPMe, where a discharge-only conversion still misses the cut-off.
    """
    ev.check_forward_and_derivatives("voltage-control")
    ev.check_forward_and_derivatives("voltage-spme")
    ev.check_flag_off("voltage-control")
    ev.check_flag_off("battery")


def test_continuation_from_starting_solution():
    """An event-sensitive starting_solution continues derivatives at the same inputs."""
    ev.check_forward_and_derivatives("resume")


def test_reuse_recomputes_for_new_inputs():
    """Reusing a simulation on different inputs recomputes the derivatives."""
    ev.check_forward_and_derivatives("reuse")


def test_copy_concat_and_state_extraction_do_not_mutate():
    """copy, chronological addition, first_state, and last_state keep aligned samples."""
    ev.check_metadata("resume")


def test_named_and_all_columns_match_alphabetical_order():
    """Named parameter columns match the alphabetical all-column stack."""
    ev.check_metadata("reuse")


def test_cycles_and_steps_expose_aligned_time_derivatives():
    """Cycles and steps expose time_sensitivities aligned with the full solution."""
    ev.check_metadata("resume")
    ev.check_metadata("reuse")


def test_explicit_time_dependent_outputs():
    """Outputs that depend on absolute time keep total phase derivatives."""
    ev.check_explicit_time("analytic")
    ev.check_explicit_time("duration")


def test_native_api_without_the_opt_in_flag():
    """Without calculate_event_sensitivities, the fixed-duration native API stays valid."""
    ev.check_forward_and_derivatives("compatibility")


def test_event_call_without_the_flag_stays_on_fixed_time():
    """An event-terminated solve without the new flag keeps the original sensitivities."""
    ev.check_forward_and_derivatives("legacy-event")


def test_dae_event_call_without_the_flag_stays_on_fixed_time():
    """The same flag-off event call stays fixed-time for an index-one DAE."""
    ev.check_forward_and_derivatives("legacy-dae")


def test_reversed_input_order_keeps_alphabetical_columns():
    """all-columns stay alphabetical, and match native all, if inputs are inserted backwards."""
    ev.check_forward_and_derivatives("order-event")
    ev.check_forward_and_derivatives("order-duration")


def test_repeated_cycles_keep_aligned_derivatives():
    """A second experiment cycle keeps the event-aligned derivatives."""
    ev.check_forward_and_derivatives("cycles")


def test_plain_solve_keeps_the_forward_solution():
    """A solve with neither sensitivity flag keeps the forward samples and times."""
    ev.check_plain("duration")
    ev.check_plain("analytic")


def test_event_flag_alone_does_not_produce_derivatives():
    """The event flag without calculate_sensitivities does not produce the new properties.

    A battery model must refuse that call the same way. Turning sensitivities on
    because the event flag is set still produces the new properties.
    """
    ev.check_event_only("analytic")
    ev.check_event_only("duration")
    ev.check_event_only("battery")
