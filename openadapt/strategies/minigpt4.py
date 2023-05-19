"""
Implements a playback strategy wherein the next ActionEvents are generated by showing
ScreenShots and ActionEvents to Minigpt-4 and giving it a text description of the goal
to be accomplished.
"""

import sys

# sys.path.append("../openadapt")
from openadapt.events import get_events
from openadapt.utils import display_event, rows2dicts
from openadapt.models import ActionEvent, Recording, Screenshot
from openadapt.strategies.ocr_mixin import OCRReplayStrategyMixin
from openadapt.strategies.base import BaseReplayStrategy

from MiniGPT4.minigpt4.common.config import Config
from MiniGPT4.minigpt4.common.dist_utils import get_rank
from MiniGPT4.minigpt4.common.registry import registry
from MiniGPT4.minigpt4.conversation.conversation import Chat, CONV_VISION

from pprint import pformat
import time

from loguru import logger
import numpy as np

import argparse
import random

import torch
import torch.backends.cudnn as cudnn

DISPLAY_EVENTS = False
REPLAY_EVENTS = True
SLEEP = True


class MiniGPT4ReplayStrategy(OCRReplayStrategyMixin, BaseReplayStrategy):
    def __init__(
        self,
        recording: Recording,
        display_events=DISPLAY_EVENTS,
        replay_events=REPLAY_EVENTS,
        sleep=SLEEP,
    ):
        super().__init__(recording)
        self.display_events = display_events
        self.replay_events = replay_events
        self.sleep = sleep
        self.prev_timestamp = None
        self.action_event_idx = -1
        self.processed_action_events = get_events(recording, process=True)
        event_dicts = rows2dicts(self.processed_action_events)
        logger.info(f"event_dicts=\n{pformat(event_dicts)}")

    def get_next_action_event(
        self,
        screenshot: Screenshot,
    ) -> ActionEvent:
        self.action_event_idx += 1
        num_action_events = len(self.processed_action_events)
        if self.action_event_idx >= num_action_events:
            # TODO: refactor
            raise StopIteration()

        # get description of the screenshot using ocr_mixin
        text = self.get_ocr_text(screenshot)

        # get prev ActionEvents text
        # TODO: may have to alter this to allow for more descriptive answers from MiniGPT-4
        previously_recorded_action_events = ""
        for event in self.processed_action_events[: self.action_event_idx]:
            if previously_recorded_action_events != "":
                previously_recorded_action_events += ", "
            previously_recorded_action_events += event.text

        # feed recording.task_description, current screenshot, and past ActionEvents to
        # MiniGPT4 to generate the next ActionEvent

        # Model Initialization from MiniGPT4 demo.py
        args = parse_args()
        cfg = Config(args)

        model_config = cfg.model_cfg
        model_config.device_8bit = args.gpu_id
        model_cls = registry.get_model_class(model_config.arch)
        model = model_cls.from_config(model_config).to("cuda:{}".format(args.gpu_id))

        vis_processor_cfg = cfg.datasets_cfg.cc_sbu_align.vis_processor.train
        vis_processor = registry.get_processor_class(
            vis_processor_cfg.name
        ).from_config(vis_processor_cfg)
        chat = Chat(model, vis_processor, device="cuda:{}".format(args.gpu_id))

        # upload screenshot
        chat_state = CONV_VISION.copy()
        img_list = []
        chat.upload_img(screenshot, chat_state, img_list)

        # ask question
        user_message = (
            "Please generate the next action event based on the following:\n\n"
            "Task goal: {}\n\n"
            "Previously recorded action events: {}\n\n"
            "Screenshot description: {}\n\n"
            "Please provide your action event below.".format(
                self.recording.task_description, previously_recorded_action_events, text
            )
        )
        chat.ask(user_message, chat_state)

        # get answer as a string
        llm_message = chat.answer(
            conv=chat_state,
            img_list=img_list,
            num_beams=1,
            temperature=1,
            max_new_tokens=300,
            max_length=2000,
        )[0]

        print(llm_message)
        # TODO: remove

        # TODO: create ActionEvent from llm_message and return it

        # TODO: might need to change this part
        action_event = self.processed_action_events[self.action_event_idx]
        logger.info(f"{self.action_event_idx=} of {num_action_events=}: {action_event=}")

        # for displaying/replaying events

        if self.display_events:
            image = display_event(action_event)
            image.show()
        if self.replay_events:
            if self.sleep and self.prev_timestamp:
                sleep_time = action_event.timestamp - self.prev_timestamp
                logger.debug(f"{sleep_time=}")
                time.sleep(sleep_time)
            self.prev_timestamp = action_event.timestamp
            return action_event
        else:
            return None


def parse_args():
    parser = argparse.ArgumentParser(description="Demo")
    parser.add_argument("--cfg-path", required=True, help="path to configuration file.")
    parser.add_argument(
        "--gpu-id", type=int, default=0, help="specify the gpu to load the model."
    )
    parser.add_argument(
        "--options",
        nargs="+",
        help="override some settings in the used config, the key-value pair "
        "in xxx=yyy format will be merged into config file (deprecate), "
        "change to --cfg-options instead.",
    )
    args = parser.parse_args()
    return args


def setup_seeds(config):
    seed = config.run_cfg.seed + get_rank()

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    cudnn.benchmark = False
    cudnn.deterministic = True
