#   Copyright (c) 2019 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and

import logging

import paddle.fluid as fluid
import paddle.fluid.io as io
import paddle.fluid.transpiler.distribute_transpiler as dist_transpiler

from paddle.fluid.incubate.fleet.base.fleet_base import Fleet
from paddle.fluid.incubate.fleet.base.fleet_base import Mode
from paddle.fluid.incubate.fleet.base.fleet_base import DistributedOptimizer

from paddle.fluid import compiler

import os
import sys


class LambConfig(object):
    def __init__(self):
        pass


class DistFCConfig(object):
    def __init__(self):
        pass


class Collective(Fleet):
    def __init__(self):
        super(Collective, self).__init__(Mode.COLLECTIVE)
        self._local_ip = 0

        self.startup_program = None
        self._origin_program = None
        self.main_program = None

    def init_worker(self):
        logging.warn(
            "You should not call 'init_worker' method for collective mode.")

    def run_worker(self, main_programs=None, scopes=None):
        logging.warn(
            "You should not call 'run_worker' method for collective mode.")

    def init_server(self, model_dir=None):
        logging.warn(
            "You should not call 'init_server' method for collective mode.")

    def run_server(self):
        logging.warn(
            "You should not call 'run_server' method for collective mode.")

    def stop_worker(self):
        logging.warn(
            "You should not call 'stop_worker' method for collective mode.")

    def distributed_optimizer(self, optimizer, strategy=None):
        self._optimizer = \
            CollectiveOptimizer(optimizer, strategy)
        return self._optimizer

    def save_inference_model(self,
                             executor,
                             dirname,
                             feeded_var_names=None,
                             target_vars=None,
                             main_program=None,
                             export_for_deployment=True):
        io.save_inference_model(dirname, feeded_var_names, target_vars,
                                executor, main_program, None, None,
                                export_for_deployment)

    def save_persistables(self, executor, dirname, main_program=None):
        io.save_persistables(executor, dirname, main_program, None)

    def node_num(self):
        return self._role_maker._node_num

    def node_id(self):
        return self._role_maker._node_id


fleet = Collective()


class DistributedStrategy(fluid.BuildStrategy):
    """
    Init function of DistributedStrategy
    """

    def __init__(self):
        super(DistributedStrategy, self).__init__()
        self.fuse_memory_size = -1
        self.fuse_layer_size = 1

        self.use_local_sgd = False
        self.use_dist_fc = False

        self.local_sgd_config = None  # LocalSGDConfig
        self.dist_fc_config = None  # DistFCConfig
        self.mode = "nccl2"  # or collective
        self.collective_mode = None  # local_sgd or grad_allreduce

        self.nccl_comm_num = 2

        self.exec_strategy = fluid.ExecutionStrategy()
        sync_allreduce = os.getenv("FLAGS_sync_nccl_allreduce")
        if sync_allreduce == "0":
            self._exec_strategy.num_threads = self.nccl_comm_num + 1
            if sef.use_hierarchical_allreduce:
                self._exec_strategy.num_threads = 2 * self.nccl_comm_num + 1
            if self._exec_strategy.num_threads > 4:
                print(
                    sys.stderr,
                    "WARNING: if you use use_hierarchical_allreduce or "
                    "with multi nccl comm, please set FLAGS_sync_nccl_allreduce = 0"
                )


class CollectiveOpBasedOptimizer(DistributedOptimizer):
    """
    Collective Operator Base Class For Distributed Optimizer
    The class is invisible to a user
    """

    def __init__(self, optimizer, strategy=None):
        assert isinstance(
            strategy,
            DistributedStrategy), "strategy must be DistributedStrategy"
        super(CollectiveOpBasedOptimizer, self).__init__(optimizer, strategy)

    def backward(self,
                 loss,
                 startup_program=None,
                 parameter_list=None,
                 no_grad_set=None,
                 callbacks=None):
        return self._optimizer.backward(loss, startup_program, parameter_list,
                                        no_grad_set, callbacks)

    def apply_gradients(self, params_grads):
        return self._optimizer.apply_gradients(params_grads)


class CollectiveOptimizer(DistributedOptimizer):
    """
    DistributedOptimizer is a wrapper for paddle.fluid.optimizer
    A user should pass a paddle.fluid.optimizer to DistributedOptimizer
    minimize() function is implemented.
    DistributedOptimizer is the starting point for a user who wants to
    run distributed training. The optimized information will be stored in
    Fleet() instance who holds the global information about current distributed
    training.
    """

    def __init__(self, optimizer, strategy=DistributedStrategy()):
        super(CollectiveOptimizer, self).__init__(optimizer, strategy)
        self.print_config = False

    def backward(self,
                 loss,
                 startup_program=None,
                 parameter_list=None,
                 no_grad_set=None,
                 callbacks=None):
        return self._optimizer.backward(loss, startup_program, parameter_list,
                                        no_grad_set, callbacks)

    def apply_gradients(self, params_grads):
        return self._optimizer.apply_gradients(params_grads)

    def _check_condition(self, name, **kwargs):
        for k, v in kwargs.iterms():
            if v is True:
                assert False, "you can't use %s and %s together" % (name, k)

    def _check_collective_mode(self, main_program, optimizer, strategy):
        """
        Check the conflict condtions.
        """
        if strategy.use_local_sgd:
            self._check_condition(
                "use_local_sgd",
                use_dgc=main_program._enable_dgc,
                use_dist_fc=strategy.use_dist_fc,
                use_lamb=main_program._use_lamb)
            assert strategy.local_sgd_config is not None, "DistributedStrategy.local_sgd_config should be set"

        if strategy.use_dist_fc:
            self._check_condition(
                "use_dist_fc",
                use_dgc=main_program._enable_dgc,
                use_local_sgd=strategy.use_local_sgd,
                use_lamb=main_program._use_lamb)
            assert strategy.dist_fc_config is not None, "DistributedStrategy.dist_fc_config should be set"

        if self._strategy.collective_mode=="local_sgd" \
                or self._strategy.collective_mode == "grad_allreduce":
            assert self._strategy.mode == "collective", \
                "local_sgd and grad_allreduce can be used under collective mode"

    def _transpile(self, startup_program, main_program):
        """
        Transpile the programs to distributed programs. And add the variables.
        """
        if self._strategy.fuse_all_reduce_ops:
            os.environ[
                'FLAGS_fuse_parameter_memory_size'] = self.fuse_memory_size
            os.environ[
                'FLAGS_fuse_parameter_groups_size'] = self.fuse_layer_size

        worker_endpoints = fleet.worker_endpoints()
        trainer_id = fleet.worker_index()
        current_endpoint = fleet.worker_endpoints()[trainer_id]
        worker_endpoints_env = ','.join(worker_endpoints)
        trainers_num = fleet.worker_num()

        if self.print_config:
            print("worker_endpoints:{} trainers_num:{} current_endpoint:{} \
                  trainer_id:{}".format(worker_endpoints, trainers_num,
                                        current_endpoint, trainer_id))

        # call transpiler
        config = dist_transpiler.DistributeTranspilerConfig()
        config.mode = self._strategy.mode
        config.collective_mode = self._strategy.collective_mode

        config.nccl_comm_num = self._strategy.nccl_comm_num
        config.use_hierarchical_allreduce = self._strategy.use_hierarchical_allreduce
        config.hierarchical_allreduce_inter_nranks = self._strategy.hierarchical_allreduce_inter_nranks

        t = dist_transpiler.DistributeTranspiler(config=config)
        t.transpile(
            trainer_id=trainer_id,
            trainers=worker_endpoints_env,
            startup_program=startup_program,
            program=main_program,
            current_endpoint=current_endpoint)

    def _try_to_compile(self, startup_program, main_program):
        self._transpile(startup_program, main_program)

        if self._strategy.mode == "collective":
            return main_program

        self._strategy.num_trainers = fleet.worker_num()
        self._strategy.trainer_id = fleet.worker_index()
        self._strategy.trainers_endpoints = fleet.worker_endpoints()
        self._strategy.enable_backward_optimizer_op_deps = True

        self._compiled_program = compiler.CompiledProgram(main_program)

        self._compiled_program.with_data_parallel(
            loss_name=self._loss.name,
            build_strategy=self._strategy,
            exec_strategy=self._strategy.exec_strategy,
            share_vars_from=None)

        return self._compiled_program

    def minimize(self,
                 loss,
                 startup_program=None,
                 parameter_list=None,
                 no_grad_set=None):
        """
        minimize a program through loss
        Args:
            loss (Variable|Variable List): loss variable or loss variable list to run optimization.
            startup_program (Program): startup_program for initializing parameters
                in `parameter_list`.
            parameter_list (list): list of Variables to update.
            no_grad_set (set|None): set of Variables should be ignored.
        Returns:
            tuple: (optimize_ops, params_grads) which are, list of operators appended;
            and list of (param, grad) Variables pair for optimization.
        Note that in parameter server mode, a worker will not get anything about optimize_os
        Because optmizer algorithms run on pserver side. We will make this usable in pserver
        process, but currently the optimization part is written into Fleet(). A user does not
        need to care about how to startup a pserver node.
        """
        main_program = loss.block.program
        if startup_program is None:
            startup_program = fluid.default_startup_program()
        fleet.startup_program = startup_program

        self._loss = loss

        self._check_collective_mode(main_program, self._optimizer,
                                    self._strategy)

        optimize_ops, param_grads = self._optimizer.minimize(
            loss, startup_program, parameter_list, no_grad_set)

        fleet._origin_program = main_program
        fleet.main_program = self._try_to_compile(startup_program, main_program)

        return optimize_ops, param_grads
