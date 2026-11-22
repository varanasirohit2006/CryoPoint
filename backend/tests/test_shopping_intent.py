import pytest

from jit_ui.config import Settings
from jit_ui.domain.intent import interpret_amazon
from jit_ui.domain.models import TaskRequest
from jit_ui.domain.sites import validate_task


@pytest.mark.parametrize("text,expected", [
    ("Go to Amazon and search for 5 years girl toys which are below 500 rupees.", {"query": "5 years girl toys", "max_price": 500}),
    ("Find LEGO toys for 5-year-old girls under Rs. 500 cheapest first", {"query": "LEGO toys for 5-year-old girls", "max_price": 500, "sort_by": "price-asc-rank"}),
    ("Show me backpacks between INR 1,000 and 2,500 rupees", {"query": "backpacks", "min_price": 1000, "max_price": 2500}),
    ("Search toys for girls between 3 and 5 years", {"query": "toys for girls between 3 and 5 years"}),
    ("Search bottles over 500 ml", {"query": "bottles over 500 ml"}),
    ("Find shoes under ₹900 with at least 4 stars and up", {"query": "shoes", "max_price": 900, "min_rating": 4}),
    ("Search dolls below 700 sort by rating", {"query": "dolls", "max_price": 700, "sort_by": "review-rank"}),
    ("go to the amazon.in and search for men formals for 20 years old . and filter it only the products with below 1000",
     {"query": "men formals for 20 years old", "max_price": 1000}),
    ("Please open the Amazon.in, and then find me shoes which having mrp less than 1200.",
     {"query": "shoes", "max_price": 1200}),
    ("Search water filter under 1000", {"query": "water filter", "max_price": 1000}),
    ("Find toys which have lights below 500", {"query": "toys which have lights", "max_price": 500}),
])
def test_flexible_requests_preserve_product_details(text, expected):
    assert interpret_amazon(text) == expected


def test_task_text_is_authoritative_and_manual_overrides_are_explicit():
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task="Find dolls below 500",
                       parameters={"query": "shoes", "max_price": 999})
    settings = Settings(_env_file=None)
    assert validate_task(task, settings).parameters == {"query": "dolls", "max_price": 500}
    manual = task.model_copy(update={"parameter_source": "explicit"})
    assert validate_task(manual, settings).parameters == {"query": "shoes", "max_price": 999}
    legacy = task.model_copy(update={"parameters": {"query": "Go to Amazon and search for toys below 450 rupees"}})
    assert validate_task(legacy, settings).parameters == {"query": "toys", "max_price": 450}
    for parameters in ({"unknown": 3}, {"query": "toys", "min_price": 800, "max_price": 500}, {"query": "toys", "min_rating": 6}):
        with pytest.raises(ValueError):
            validate_task(task.model_copy(update={"parameter_source": "explicit", "parameters": parameters}), settings)


def test_command_in_manual_query_is_cleaned_and_manual_prices_stay_authoritative():
    command = "go to the amazon.in and search for men formals for 20 years old . and filter it only the products with below 1000"
    task = TaskRequest(site_id="amazon_in", goal_id="search_products", task=command, parameter_source="explicit",
                       parameters={"query": "  " + command + "  ", "min_price": 500, "max_price": 800})
    settings = Settings(_env_file=None)
    assert validate_task(task, settings).parameters == {"query": "men formals for 20 years old", "min_price": 500, "max_price": 800}
    plain = task.model_copy(update={"parameters": {"query": " men   formals 20 year old  "}})
    assert validate_task(plain, settings).parameters == {"query": "men formals 20 year old"}
