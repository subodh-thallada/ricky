from bench.services.feature_options import _apply_recommendation, _parse_options


def test_recommendation_marks_option_matching_remembered_preferences():
    options = _parse_options(
        """
        {"suggestions":[
          {"id":"simple","title":"Simple explicit implementation","summary":"Clear loop with readable control flow.","implementationPlan":"Keep it small and straightforward.","tradeoffs":["Very readable"],"generatedCode":"def f(): pass"},
          {"id":"fast","title":"Fast cached implementation","summary":"Uses caching for performance.","implementationPlan":"Add cache state for repeated calls.","tradeoffs":["Faster runtime"],"generatedCode":"def f(): pass"}
        ]}
        """
    )
    memories = [
        {
            "content": (
                "Bench implementation choice accepted. Chosen option: Readable iterative approach. "
                "Preference signal: favor readable, clear, simple, straightforward implementations."
            )
        }
    ]

    _apply_recommendation(options, memories)

    recommended = [option for option in options if option.recommended]
    assert [option.id for option in recommended] == ["simple"]
    assert recommended[0].recommendationReason


def test_recommendation_is_absent_without_memories():
    options = _parse_options(
        """
        {"suggestions":[
          {"id":"simple","title":"Simple implementation","summary":"Clear.","implementationPlan":"Keep it small.","tradeoffs":[],"generatedCode":"def f(): pass"}
        ]}
        """
    )

    _apply_recommendation(options, [])

    assert not options[0].recommended
    assert options[0].recommendationReason is None
