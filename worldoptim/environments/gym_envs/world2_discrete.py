import numpy as np
import gym
from worldoptim.environments.gym_envs.base_env import BaseEnv
from worldoptim.environments.models.utils.pyworld import plot_world_state


class World2Discrete(BaseEnv):
    def __init__(self,
                 cost_function,
                 model,
                 simulation_horizon,
                 time_action_start=70,
                 percentage_shift=0.05,
                 time_resolution=1,  # 1 year
                 seed=np.random.randint(1e6)
                 ):
        """
        World2Discrete is based on the World2 dynamical model.

        Parameters
        ----------
        cost_function: BaseCostFunction
            A cost function.
        model: BaseModel
            An epidemiological model.
        simulation_horizon: int
            Simulation horizon in years.
        time_resolution: int
            In years.
        """

        # Initialize model
        self.model = model
        self.stochastic = self.model.stochastic
        self.simulation_horizon = simulation_horizon
        self.reset_same = False  # whether the next reset resets the same dynamical model
        self.time_action_start = time_action_start
        self.percentage_shift = percentage_shift
        self.dim_action = 5

        # Initialize cost function
        self.cost_function = cost_function
        self.nb_costs = cost_function.nb_costs
        self.cumulative_costs = [0 for _ in range(self.nb_costs)]

        # Initialize states
        self.state_labels = self.model.state_labels_for_agent +\
                            ['cumulative_cost_{}'.format(id_cost) for id_cost in range(self.cost_function.nb_costs)] # + []
        self.label_to_id = dict(zip(self.state_labels, np.arange(len(self.state_labels))))
        self.normalization_factors = [1] * len(self.state_labels)
        self.time_resolution = time_resolution

        super().__init__(cost_function=cost_function,
                         model=model,
                         simulation_horizon=simulation_horizon,
                         dim_action=5,
                         discrete=True,
                         seed=seed)

        self._max_episode_steps = simulation_horizon // time_resolution
        self.history = None

    def _update_previous_env_state(self):
        """
        Save previous env state.

        """
        if self.env_state is not None:
            self.previous_env_state = self.env_state.copy()
            self.previous_env_state_labelled = self.env_state_labelled.copy()

    def _update_env_state(self):
        """
        Update the environment state.

        """

        # Update env state
        self.env_state_labelled = dict()
        for k in self.model.state_labels_for_agent:
            index = self.model.internal_states_labels.index(k)
            self.env_state_labelled[k] = self.model_state[index]

        for id_cost in range(self.nb_costs):
            self.env_state_labelled['cumulative_cost_{}'.format(id_cost)] = self.cumulative_costs[id_cost]
        assert sorted(list(self.env_state_labelled.keys())) == sorted(self.state_labels), "labels do not match"
        self.env_state = np.array([self.env_state_labelled[k] for k in self.state_labels])

        # Set previous env state to env state if first step
        if self.previous_env_state is None:
            # happens at first step
            self.previous_env_state = self.env_state.copy()
            self.previous_env_state_labelled = self.env_state_labelled.copy()

    def reset_same_model(self):
        """
        To call if you want to reset to the same model the next time you call reset.
        Will be cancelled after the first reset, it needs to be called again each time.


        """
        self.reset_same = True

    def reset(self):
        """
        Reset the environment and the tracking of data.

        Returns
        -------
        nd.array
            The initial environment state.

        """

        # initialize history of states, internal model states, actions, cost_functions, deaths
        self.history = dict(env_states=[],
                            model_states=[],
                            env_timesteps=[],
                            actions=[],
                            aggregated_costs=[],
                            costs=[])
        # initialize time and lockdown days counter
        self.t = 0
        self.cumulative_costs = [0 for _ in range(self.nb_costs)]

        # initialize model internal state and params
        if self.reset_same:
            self.model.reset_same_model()
            self.reset_same = False
        else:
            self.model.reset()
        self.model_state = self.model._get_current_state()

        self._update_previous_env_state()
        self._update_env_state()

        self.history['env_states'].append(self.env_state.copy())
        self.history['model_states'].append(self.model_state.copy().tolist())
        self.history['env_timesteps'].append(self.t)

        for _ in range(self.time_action_start):
            self.step(action=[0] * self.dim_action)

        return self._normalize_env_state(self.env_state)

    def update_with_action(self, action):
        """
        Implement effect of action on transmission rate.

        Parameters
        ----------
        action: int
            Action is 0 (no lock-down) or 1 (lock-down).

        """
        # add or substract self.percentage_shift percent of the control state
        for i_v, v in enumerate(self.model.control_variables):
            assert action[i_v] in [-1, 0, 1]
            if action[i_v] == 1:
                self.model.current_internal_params[v] += self.percentage_shift * self.model.current_internal_params[v]
            elif action[i_v] == -1:
                self.model.current_internal_params[v] -= self.percentage_shift * self.model.current_internal_params[v]
            # clip them to reasonable values
            self.model.current_internal_params[v] = np.clip(self.model.current_internal_params[v], *self.model.control_variables_ranges[i_v])

    def step(self, action):
        """
        Traditional step function from OpenAI Gym envs. Uses the action to update the environment.

        Parameters
        ----------
        action: int
            Action is 0 (no lock-down) or 1 (lock-down).


        Returns
        -------
        state: nd.array
            New environment state.
        cost_aggregated: float
            Aggregated measure of the cost.
        done: bool
            Whether the episode is terminated.
        info: dict
            Further infos. In our case, the costs, icu capacity of the region and whether constraints are violated.

        """

        self.jump_of = min(self.time_resolution, self.simulation_horizon - self.t)

        if self.t == 70:
            self.model.current_internal_params['NRUN'] = 0.25
        # self.update_with_action(action)

        # Run model for jump_of steps
        model_state = [self.model_state]
        model_states = []
        model_state = self.model.run_n_steps(model_state[-1], 1)
        model_states += model_state.tolist()
        self.model_state = model_state[-1]  # last internal state is the new current one
        self.t += self.jump_of

        # Update state
        self._update_previous_env_state()
        self._update_env_state()

        # Store history
        costs = [c.compute_cost(previous_state=np.atleast_2d(self.previous_env_state),
                                state=np.atleast_2d(self.env_state),
                                label_to_id=self.label_to_id,
                                action=action,
                                others=dict(jump_of=self.time_resolution))[0] for c in self.cost_function.costs]
        for i in range(len(costs)):
            self.cumulative_costs[i] += costs[i]

        self._update_env_state()

        self.history['actions'] += [action] * self.jump_of
        self.history['env_states'] += [self.env_state.copy()] * self.jump_of
        self.history['env_timesteps'] += list(range(self.t - self.jump_of, self.t))
        self.history['model_states'] += [self.model_state.copy()]


        # Compute cost_function
        cost_aggregated, costs, over_constraints = self.cost_function.compute_cost(previous_state=self.previous_env_state,
                                                                                   state=self.env_state,
                                                                                   label_to_id=self.label_to_id,
                                                                                   action=action,
                                                                                   others=dict(jump_of=self.jump_of))
        costs = costs.flatten()

        self.history['aggregated_costs'] += [cost_aggregated / self.jump_of] * self.jump_of
        self.history['costs'] += [costs / self.jump_of for _ in range(self.jump_of)]
        self.costs = costs.copy()

        if self.t >= self.simulation_horizon:
            done = 1
        else:
            done = 0

        return self._normalize_env_state(self.env_state), cost_aggregated, done, dict(costs=costs,
                                                                                      constraints=over_constraints.flatten())

    # Utils
    def sample_action(self):
        return np.random.choice([-1, 0, 1], size=self.dim_action)

    def _normalize_env_state(self, env_state):
        return (env_state / np.array(self.normalization_factors)).copy()

    # def _set_rew_params(self, goal):
    #     self.cost_function.set_goal_params(goal.copy())
    #
    # def sample_cost_function_params(self):
    #     return self.cost_function.sample_goal_params()

    # Format data for plotting
    def get_data(self):

        data = dict(history=self.history.copy(),
                    time_jump=1,
                    model_states_labels=self.model.state_labels_for_agent)
        t = [time + self.model.year_min for time in self.history['env_timesteps']]
        betas = [0, 0.25, 0.5, 0.75, 1]
        costs = np.concatenate([np.array([[0, 0]]),np.array(self.history['costs'])])
        aggregated = [self.cost_function.compute_aggregated_cost(costs, beta) for beta in betas]
        control_variables = [np.array(self.history['env_states'])[:, self.state_labels.index(k)] for k in self.model.control_variables]
        control_labels = self.model.control_variables
        stocks = [np.array(self.history['env_states'])[:, self.state_labels.index(k)] for k in self.model.stocks]
        stocks_labels = self.model.stocks
        rates = [np.array(self.history['env_states'])[:, self.state_labels.index(k)] for k in self.model.rates]
        rates_labels = self.model.rates
        death_rate = np.array(self.history['env_states'])[:, self.state_labels.index('DR')]
        qol = np.array(self.history['env_states'])[:, self.state_labels.index('QL')]
        to_plot = [np.array(control_variables).transpose(),
                   np.array(stocks).transpose(),
                   np.array(rates).transpose(),
                   ]
        to_plot2 = [costs,
                    death_rate,
                    qol,
                    np.array(aggregated).transpose()]
        labels = ['Control variables', 'Stocks', 'Rates']
        labels2 = ['Costs', 'Death rate', 'Quality of Life', 'Aggregated costs']
        legends = [control_labels, stocks_labels, rates_labels]
        legends2 = [['Death rate cost', 'QoL cost'], None, None, [r'$\beta = $' + str(beta) for beta in betas],]
        stats_run = dict(to_plot=to_plot,
                         labels=labels,
                         legends=legends,
                         time=t)
        stats_run2 = dict(to_plot=to_plot2,
                         labels=labels2,
                         legends=legends2,
                         time=t)
        world_states_labels = ['P', 'POLR', 'CI', 'QL', 'NR']
        world_states = np.array([np.array(self.history['model_states'])[:, self.model.internal_states_labels.index(k)] for k in world_states_labels]).transpose()
        data['world_stats'] = dict(states=world_states, labels=world_states_labels)
        data['stats_run'] = stats_run
        data['stats_run2'] = stats_run2
        data['title'] = 'Results'
        return data


if __name__ == '__main__':
    from worldoptim.utils import plot_stats
    from worldoptim.environments.cost_functions import get_cost_function
    from worldoptim.environments.models import get_model

    simulation_horizon = 200
    action_start = 71
    stochastic = False

    model = get_model(model_id='world2', params=dict(stochastic=stochastic))

    cost_function = get_cost_function(cost_function_id='multi_cost_deathrate_qol', params=dict())

    env = gym.make('World2Discrete-v0',
                   cost_function=cost_function,
                   time_action_start=action_start,
                   model=model,
                   simulation_horizon=simulation_horizon)
    env.reset()


    actions = [env.unwrapped.sample_action() for _ in range(simulation_horizon - action_start)]

    done = False
    t = 0
    while not done:
        out = env.step(actions[t])
        t += 1
        done = out[2]
    stats = env.unwrapped.get_data()

    # plot model states
    # plot_stats(t=stats['history']['env_timesteps'],
    #            states=np.array(stats['history']['model_states']).transpose(),
    #            labels=stats['model_states_labels'],
    #            time_jump=stats['time_jump'])
    plot_world_state(time=stats['stats_run']['time'], states=stats['world_stats']['states'], show=False)

    plot_stats(t=stats['stats_run2']['time'],
               states=stats['stats_run2']['to_plot'],
               labels=stats['stats_run2']['labels'],
               legends=stats['stats_run2']['legends'],
               title=stats['title'],
               time_jump=stats['time_jump'],
               )
    plot_stats(t=stats['stats_run']['time'],
               states=stats['stats_run']['to_plot'],
               labels=stats['stats_run']['labels'],
               legends=stats['stats_run']['legends'],
               title=stats['title'],
               time_jump=stats['time_jump'],
               show=True
               )
