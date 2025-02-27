import json
import logging
import math
import random
from operator import itemgetter
from typing import Any

import numpy as np
import ray

from agentsociety.environment import Simulator
from agentsociety.llm import LLM
from agentsociety.memory import Memory
from agentsociety.workflow import Block, FormatPrompt

from .dispatcher import BlockDispatcher
from .utils import clean_json_response

logger = logging.getLogger("agentsociety")

# Prompt templates for LLM interactions
PLACE_TYPE_SELECTION_PROMPT = """
As an intelligent decision system, please determine the type of place the user needs to visit based on their input requirement.
User Plan: {plan}
User requirement: {intention}
Your output must be a single selection from {poi_category} without any additional text or explanation.

Please response in json format (Do not return any other text), example:
{{
    "place_type": "shopping"
}}
"""

PLACE_SECOND_TYPE_SELECTION_PROMPT = """
As an intelligent decision system, please determine the type of place the user needs to visit based on their input requirement.
User Plan: {plan}
User requirement: {intention}
Your output must be a single selection from {poi_category} without any additional text or explanation.

Please response in json format (Do not return any other text), example:
{{
    "place_type": "shopping"
}}
"""

PLACE_ANALYSIS_PROMPT = """
As an intelligent analysis system, please determine the type of place the user needs to visit based on their input requirement.
User Plan: {plan}
User requirement: {intention}

Your output must be a single selection from ['home', 'workplace', 'other'] without any additional text or explanation.

Please response in json format (Do not return any other text), example:
{{
    "place_type": "home"
}}
"""

RADIUS_PROMPT = """As an intelligent decision system, please determine the maximum travel radius (in meters) based on the current emotional state.

Current weather: {weather}
Current temperature: {temperature}
Your current emotion: {emotion_types}
Your current thought: {thought}

Please analyze how these emotions would affect travel willingness and return only a single integer number between 3000-200000 representing the maximum travel radius in meters. A more positive emotional state generally leads to greater willingness to travel further.

Please response in json format (Do not return any other text), example:
{{
    "radius": 10000
}}
"""


def gravity_model(pois):
    """
    Calculate selection probabilities for POIs using a gravity model.

    The model considers both distance decay (prefer closer locations)
    and spatial density (avoid overcrowded areas). Distances are grouped
    into 1km bins up to 10km, with POIs beyond 10km in a 'more' category.

    Args:
        pois: List of POI tuples containing (poi_data, distance)

    Returns:
        List of tuples: (name, id, normalized_weight, distance)
        with selection probabilities based on gravity model
    """
    # Initialize distance bins
    pois_Dis = {f"{d}k": [] for d in range(1, 11)}
    pois_Dis["more"] = []

    # Categorize POIs into distance bins
    for poi in pois:
        classified = False
        for d in range(1, 11):
            if (d - 1) * 1000 <= poi[1] < d * 1000:
                pois_Dis[f"{d}k"].append(poi)
                classified = True
                break
        if not classified:
            pois_Dis["more"].append(poi)

    res = []
    distanceProb = []
    # Calculate weights for each POI
    for poi in pois:
        for d in range(1, 11):
            if (d - 1) * 1000 <= poi[1] < d * 1000:
                n = len(pois_Dis[f"{d}k"])
                # Calculate ring area between (d-1)km and d km
                S = math.pi * ((d * 1000) ** 2 - ((d - 1) * 1000) ** 2)
                density = n / S  # POIs per square meter
                distance = max(poi[1], 1)  # Avoid division by zero

                # Inverse square distance decay combined with density
                weight = density / (distance**2)
                res.append((poi[0]["name"], poi[0]["id"], weight, distance))
                distanceProb.append(1 / math.sqrt(distance))
                break

    # Normalize probabilities and sample
    distanceProb = np.array(distanceProb)
    distanceProb /= distanceProb.sum()

    # Randomly sample 50 candidates weighted by distance probabilities
    sample_indices = np.random.choice(len(res), size=50, p=distanceProb)
    sampled_pois = [res[i] for i in sample_indices]

    # Normalize weights for final selection
    total_weight = sum(item[2] for item in sampled_pois)
    return [
        (item[0], item[1], item[2] / total_weight, item[3]) for item in sampled_pois
    ]


class PlaceSelectionBlock(Block):
    """
    Block for selecting destinations based on user intention.

    Implements a two-stage selection process:
    1. Select primary POI category (e.g., 'shopping')
    2. Select sub-category (e.g., 'bookstore')
    Uses LLM for decision making with fallback to random selection.

    Configurable Fields:
        search_limit: Max number of POIs to retrieve from map service
    """

    configurable_fields = ["search_limit"]
    default_values = {"search_limit": 50}

    def __init__(self, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__(
            "PlaceSelectionBlock", llm=llm, memory=memory, simulator=simulator
        )
        self.description = (
            "Selects destinations for unknown locations (excluding home/work)"
        )
        self.typeSelectionPrompt = FormatPrompt(PLACE_TYPE_SELECTION_PROMPT)
        self.secondTypeSelectionPrompt = FormatPrompt(
            PLACE_SECOND_TYPE_SELECTION_PROMPT
        )
        self.radiusPrompt = FormatPrompt(RADIUS_PROMPT)
        self.search_limit = 50  # Default config value

    async def forward(self, step, context):  # type:ignore
        """Execute the destination selection workflow"""
        # Stage 1: Select primary POI category
        poi_cate = self.simulator.get_poi_cate()
        self.typeSelectionPrompt.format(
            plan=context["plan"],
            intention=step["intention"],
            poi_category=list(poi_cate.keys()),
        )
        try:
            # LLM-based category selection
            levelOneType = await self.llm.atext_request(
                self.typeSelectionPrompt.to_dialog(),
                response_format={"type": "json_object"},
            )
            levelOneType = json.loads(clean_json_response(levelOneType))[  # type:ignore
                "place_type"
            ]
            sub_category = poi_cate[levelOneType]
        except Exception as e:
            logger.warning(f"Level 1 selection failed: {e}")
            levelOneType = random.choice(list(poi_cate.keys()))
            sub_category = poi_cate[levelOneType]

        # Stage 2: Select sub-category
        try:
            self.secondTypeSelectionPrompt.format(
                plan=context["plan"],
                intention=step["intention"],
                poi_category=sub_category,
            )
            levelTwoType = await self.llm.atext_request(
                self.secondTypeSelectionPrompt.to_dialog(),
                response_format={"type": "json_object"},
            )
            levelTwoType = json.loads(clean_json_response(levelTwoType))[  # type:ignore
                "place_type"
            ]
        except Exception as e:
            logger.warning(f"Level 2 selection failed: {e}")
            levelTwoType = random.choice(sub_category)

        # Get travel radius from LLM
        try:
            self.radiusPrompt.format(
                emotion_types=await self.memory.status.get("emotion_types"),
                thought=await self.memory.status.get("thought"),
                weather=self.simulator.sence("weather"),
                temperature=self.simulator.sence("temperature"),
            )
            radius = await self.llm.atext_request(
                self.radiusPrompt.to_dialog(), response_format={"type": "json_object"}
            )
            radius = int(json.loads(radius)["radius"])  # type:ignore
        except Exception as e:
            logger.warning(f"Radius selection failed: {e}")
            radius = 10000  # Default 10km

        # Query and select POI
        center = (await self.memory.status.get("position")).values()
        pois = ray.get(
            self.simulator.map.query_pois.remote(  # type:ignore
                center=center,
                category_prefix=levelTwoType,
                radius=radius,
                limit=self.search_limit,
            )
        )

        if pois:
            pois = gravity_model(pois)
            probabilities = [item[2] for item in pois]
            selected = np.random.choice(len(pois), p=probabilities)
            next_place = (pois[selected][0], pois[selected][1])
        else:  # Fallback random selection
            all_pois = ray.get(self.simulator.map.get_pois.remote())  # type:ignore
            next_place = random.choice(all_pois)

        context["next_place"] = next_place
        node_id = await self.memory.stream.add_mobility(
            description=f"For {step['intention']}, selected: {next_place}"
        )
        return {
            "success": True,
            "evaluation": f"Selected destination: {next_place}",
            "consumed_time": 5,
            "node_id": node_id,
        }


class MoveBlock(Block):
    """Block for executing mobility operations (home/work/other)"""

    def __init__(self, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__("MoveBlock", llm=llm, memory=memory, simulator=simulator)
        self.description = "Executes mobility operations between locations"
        self.placeAnalysisPrompt = FormatPrompt(PLACE_ANALYSIS_PROMPT)

    async def forward(self, step, context):  # type:ignore
        """Execute movement based on place analysis"""
        agent_id = await self.memory.status.get("id")
        response = await self._get_place_type(context["plan"], step["intention"])

        if response == "home":
            return await self._move_home(agent_id)
        elif response == "workplace":
            return await self._move_work(agent_id)
        else:
            return await self._move_custom(context, agent_id)

    async def _get_place_type(self, plan, intention):
        """Determine destination type via LLM"""
        try:
            self.placeAnalysisPrompt.format(plan=plan, intention=intention)
            response = await self.llm.atext_request(
                self.placeAnalysisPrompt.to_dialog(),
                response_format={"type": "json_object"},
            )
            return json.loads(clean_json_response(response))[  # type:ignore
                "place_type"
            ]
        except Exception as e:
            logger.warning(f"Place analysis failed: {e}")
            return "home"  # Default to home

    async def _move_home(self, agent_id):
        """Handle home movement logic"""
        home_id = (await self.memory.status.get("home"))["aoi_position"]["aoi_id"]
        current_pos = await self.memory.status.get("position")

        if current_pos.get("aoi_position", {}).get("aoi_id") == home_id:
            return self._stationary_result("home", home_id)

        await self.simulator.set_aoi_schedules(agent_id, home_id)
        await self._increment_poi_counter()
        return self._mobility_result("home", home_id, 45)

    async def _move_work(self, agent_id):
        """Handle workplace movement logic"""
        work_id = (await self.memory.status.get("work"))["aoi_position"]["aoi_id"]
        current_pos = await self.memory.status.get("position")

        if current_pos.get("aoi_position", {}).get("aoi_id") == work_id:
            return self._stationary_result("workplace", work_id)

        await self.simulator.set_aoi_schedules(agent_id, work_id)
        await self._increment_poi_counter()
        return self._mobility_result("workplace", work_id, 45)

    async def _move_custom(self, context, agent_id):
        """Handle custom destination movement"""
        next_place = context.get("next_place")
        if not next_place:
            next_place = await self._random_poi()

        await self.simulator.set_aoi_schedules(agent_id, next_place[1])
        await self._increment_poi_counter()
        return self._mobility_result(f"destination: {next_place[0]}", next_place[1], 45)

    async def _random_poi(self):
        """Select random POI as fallback"""
        aois = ray.get(self.simulator.map.get_aoi.remote())  # type:ignore
        while True:
            aoi = random.choice(aois)
            if aoi["poi_ids"]:
                poi_id = random.choice(aoi["poi_ids"])
                return ray.get(self.simulator.map.get_poi.remote(poi_id))  # type:ignore

    async def _increment_poi_counter(self):
        """Update POI visit counter"""
        count = await self.memory.status.get("number_poi_visited")
        await self.memory.status.update("number_poi_visited", count + 1)

    def _stationary_result(self, place_type, place_id):
        """Return result when already at destination"""
        return {
            "success": True,
            "evaluation": f"Already at {place_type}",
            "to_place": place_id,
            "consumed_time": 0,
        }

    def _mobility_result(self, desc, place_id, time):
        """Standardized success result format"""
        return {
            "success": True,
            "evaluation": f"Moved to {desc}",
            "to_place": place_id,
            "consumed_time": time,
        }


class MobilityNoneBlock(Block):
    """Null operation block for completed/failed mobility actions"""

    def __init__(self, llm: LLM, memory: Memory):
        super().__init__("MobilityNoneBlock", llm=llm, memory=memory)
        self.description = "Handles completed mobility operations"

    async def forward(self, step, context):  # type:ignore
        """Log completion without action"""
        node_id = await self.memory.stream.add_mobility(
            description=f"I finished {step['intention']}"
        )
        return {
            "success": True,
            "evaluation": f"Finished executing {step['intention']}",
            "consumed_time": 0,
            "node_id": node_id,
        }


class MobilityBlock(Block):
    """
    Main mobility coordination block.

    Orchestrates:
    - PlaceSelectionBlock: Destination selection
    - MoveBlock: Physical movement
    - MobilityNoneBlock: Completion handling
    Uses BlockDispatcher to route requests to appropriate sub-blocks.
    """

    def __init__(self, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__("MobilityBlock", llm=llm, memory=memory, simulator=simulator)
        # init all blocks
        self.place_selection_block = PlaceSelectionBlock(llm, memory, simulator)
        self.move_block = MoveBlock(llm, memory, simulator)
        self.mobility_none_block = MobilityNoneBlock(llm, memory)
        self.trigger_time = 0  # Block invocation counter
        self.token_consumption = 0  # LLM token tracker

        # Initialize block routing system
        self.dispatcher = BlockDispatcher(llm)
        self.dispatcher.register_blocks(
            [self.place_selection_block, self.move_block, self.mobility_none_block]
        )

    async def forward(self, step, context):  # type:ignore
        """Main entry point - delegates to sub-blocks"""
        self.trigger_time += 1
        # Select the appropriate sub-block using dispatcher
        selected_block = await self.dispatcher.dispatch(step)

        # Execute the selected sub-block and get the result
        result = await selected_block.forward(step, context)  # type: ignore

        return result
