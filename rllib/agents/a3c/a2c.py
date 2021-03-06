import math

from ray.rllib.agents.a3c.a3c import DEFAULT_CONFIG as A3C_CONFIG, \
    validate_config, get_policy_class
from ray.rllib.optimizers import SyncSamplesOptimizer, MicrobatchOptimizer
from ray.rllib.agents.a3c.a3c_tf_policy import A3CTFPolicy
from ray.rllib.agents.trainer_template import build_trainer
from ray.rllib.utils import merge_dicts
from ray.rllib.utils.experimental_dsl import (
    ParallelRollouts, ConcatBatches, ComputeGradients, AverageGradients,
    ApplyGradients, TrainOneStep, StandardMetricsReporting)

A2C_DEFAULT_CONFIG = merge_dicts(
    A3C_CONFIG,
    {
        "sample_batch_size": 20,
        "min_iter_time_s": 10,
        "sample_async": False,

        # A2C supports microbatching, in which we accumulate gradients over
        # batch of this size until the train batch size is reached. This allows
        # training with batch sizes much larger than can fit in GPU memory.
        # To enable, set this to a value less than the train batch size.
        "microbatch_size": None,
    },
)


def choose_policy_optimizer(workers, config):
    if config["microbatch_size"]:
        return MicrobatchOptimizer(
            workers,
            train_batch_size=config["train_batch_size"],
            microbatch_size=config["microbatch_size"])
    else:
        return SyncSamplesOptimizer(
            workers, train_batch_size=config["train_batch_size"])


# Experimental pipeline-based impl; enable with "use_pipeline_impl": True.
def training_pipeline(workers, config):
    rollouts = ParallelRollouts(workers, mode="bulk_sync")

    if config["microbatch_size"]:
        num_microbatches = math.ceil(
            config["train_batch_size"] / config["microbatch_size"])
        # In microbatch mode, we want to compute gradients on experience
        # microbatches, average a number of these microbatches, and then apply
        # the averaged gradient in one SGD step. This conserves GPU memory,
        # allowing for extremely large experience batches to be used.
        train_op = (
            rollouts.combine(
                ConcatBatches(min_batch_size=config["microbatch_size"]))
            .for_each(ComputeGradients(workers))  # (grads, info)
            .batch(num_microbatches)  # List[(grads, info)]
            .for_each(AverageGradients())  # (avg_grads, info)
            .for_each(ApplyGradients(workers)))
    else:
        # In normal mode, we execute one SGD step per each train batch.
        train_op = rollouts \
            .combine(ConcatBatches(
                min_batch_size=config["train_batch_size"])) \
            .for_each(TrainOneStep(workers))

    return StandardMetricsReporting(train_op, workers, config)


A2CTrainer = build_trainer(
    name="A2C",
    default_config=A2C_DEFAULT_CONFIG,
    default_policy=A3CTFPolicy,
    get_policy_class=get_policy_class,
    make_policy_optimizer=choose_policy_optimizer,
    validate_config=validate_config,
    training_pipeline=training_pipeline)
