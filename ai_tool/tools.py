def aitool(input_schema: dict, output_schema: dict, required_inputs: list = None):
    def decorator(func):
        func._ai_tool = {
            "input_schema": {
                "type": "object",
                "properties": input_schema,
                "required": required_inputs or [],
            },
            "output_schema": {
                "type": "object",
                "properties": output_schema,
            },
        }
        return func

    return decorator
