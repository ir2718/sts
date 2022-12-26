from transformers import AutoTokenizer, AutoModelForSequenceClassification, DataCollatorWithPadding, TrainingArguments, Trainer, EarlyStoppingCallback
from ray.tune.schedulers import PopulationBasedTraining
from ray import tune
from utils import *
from parsing import parse_fine_tune
from configs import FROZEN_CFG
import os

args = parse_fine_tune()

FROZEN_CFG.set_args(args)
set_seed_(FROZEN_CFG.SEED)
device = set_device()

dataset = load_dataset_from_huggingface(DATASET_PATH, CONFIG_NAME, args.dataset_path)

model_name = FROZEN_CFG.MODEL_NAME
tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
tokenizer_kwargs = {
    'tokenizer': tokenizer,
}
tokenized_datasets = dataset.map(
    tokenize_function, 
    batched=True, 
    fn_kwargs=tokenizer_kwargs,
)

data_collator = DataCollatorWithPadding(tokenizer=tokenizer)

def model_init():
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, 
        num_labels=1,
    )
    freeze_encoder(model)
    return model



output_dir = os.path.join('..\\frozen', model_name.replace('/', '-'))
training_args = TrainingArguments(
    evaluation_strategy='epoch',
    save_strategy='epoch',
    logging_strategy='epoch',
    optim=FROZEN_CFG.OPTIM,
    learning_rate=FROZEN_CFG.LEARNING_RATE_START,
    per_device_train_batch_size=FROZEN_CFG.TRAIN_BATCH_SIZE,
    per_device_eval_batch_size=FROZEN_CFG.VAL_BATCH_SIZE,
    num_train_epochs=FROZEN_CFG.EPOCHS,
    output_dir=output_dir,
    weight_decay=FROZEN_CFG.WEIGHT_DECAY,
    lr_scheduler_type=FROZEN_CFG.SCHEDULER,
    warmup_ratio=FROZEN_CFG.WARMUP_RATIO,
    fp16=True,
    load_best_model_at_end=True,
    metric_for_best_model=FROZEN_CFG.OPT_METRIC,
)


early_stopping = EarlyStoppingCallback(early_stopping_patience=FROZEN_CFG.PATIENCE)
trainer = Trainer(
    model=None,
    args=training_args,
    model_init=model_init,
    train_dataset=tokenized_datasets['train'],
    eval_dataset=tokenized_datasets['validation'],
    compute_metrics=compute_metrics,
    tokenizer=tokenizer,
    data_collator=data_collator,
    callbacks= [early_stopping]
)

hp_space = {
    'learning_rate': tune.choice([1e-3, 5e-4, 1e-4, 5e-5, 1e-5]),
    'per_device_train_batch_size': tune.choice([8, 16, 32]),
    'weight_decay': tune.choice([1e-2, 1e-3, 1e-4])
}

scheduler = PopulationBasedTraining(
    time_attr='training_iteration',
    mode='max',
    metric='objective',
)


opt_metric = FROZEN_CFG.OPT_METRIC
best_run = trainer.hyperparameter_search(
    local_dir='..\\frozen',
    hp_space=lambda _: hp_space,
    direction='maximize',
    backend='ray',
    compute_objective=lambda m: m[opt_metric],
    scheduler=scheduler,
    keep_checkpoints_num=1,
    verbose=0,
    reuse_actors=True,
    n_trials=10,
)

print('starting finetuning with frozen encoder . . .')
for n, v in best_run.hyperparameters.items():
    setattr(trainer.args, n, v)

trainer.train()
predict_and_save_results(trainer, tokenized_datasets, output_dir, best_run)