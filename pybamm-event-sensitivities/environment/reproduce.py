"""Analytic hybrid experiment used to distinguish sensitivity conventions."""
import os
os.environ['PYBAMM_DISABLE_TELEMETRY']='true'
import pybamm
import numpy as np

def run(p=2.,sens=False):
    model=pybamm.BaseModel()
    model.summary_variables=[]
    q=pybamm.Variable('q');z=pybamm.Variable('z')
    current=pybamm.Parameter('Current function [A]')
    capacity=pybamm.InputParameter('capacity')
    model.rhs={q:-current/capacity,z:(q-z)/(3*capacity)}
    model.initial_conditions={q:4,z:4}
    model.variables={'q':q,'z':z,'Battery voltage [V]':q,'Current [A]':current}
    parameters=pybamm.ParameterValues({'Current function [A]':1,'Nominal cell capacity [A.h]':1,
        'Initial temperature [K]':298.15,'Ambient temperature [K]':298.15})
    experiment=pybamm.Experiment(['Discharge at 1 A until 3.5 V','Rest for 2 seconds'])
    sim=pybamm.Simulation(model,parameter_values=parameters,experiment=experiment,
        solver=pybamm.IDAKLUSolver(rtol=1e-10,atol=1e-10))
    return sim.solve(inputs={'capacity':p},calculate_sensitivities=sens,calc_esoh=False)

if __name__=='__main__':
    sol=run(sens=True)
    h=1e-4;plus=run(2+h);minus=run(2-h)
    print('event time:',sol.cycles[0].t[-1], 'analytic',1.)
    print('event time FD:',(plus.cycles[0].t[-1]-minus.cycles[0].t[-1])/(2*h),'analytic',.5)
    for name in ['q','z']:
        print(name,'stored final sensitivity',np.asarray(sol[name].sensitivities['capacity'])[-1,0],
            'moving endpoint FD',(plus[name].entries[-1]-minus[name].entries[-1])/(2*h),
            'fixed time FD',(plus[name](2.)-minus[name](2.))/(2*h))
