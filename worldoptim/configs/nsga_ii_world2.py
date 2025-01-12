import numpy as np

params = dict(expe_name='',
              trial_id=0,
              env_id='World2Discrete-v0',
              seed=int(np.random.randint(1e6)),
              num_train_steps=500*1e3,
              simulation_horizon=300,
              env_params=dict(time_action_start=122),
              algo_id='NSGAII',
              agg='mean',
              algo_params=dict(eval_and_log_every=50,
                               save_policy_every=1000,
                               nb_gens=20,
                               popsize=30,
                               layers=(64,),
                               policy='nn',
                               n_evals_if_stochastic=30),
              model_id='world2',
              model_params=dict(stochastic=False),
              cost_id='multi_cost_deathrate_qol',
              cost_params=dict(),
              )
