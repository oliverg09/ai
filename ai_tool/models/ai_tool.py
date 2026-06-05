# Copyright 2026 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from datetime import date, datetime

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.mail import html_sanitize, plaintext2html
from odoo.tools.safe_eval import safe_eval

from ..tools import aitool

try:
    import markdown
except ImportError:
    markdown = None


class AiTool(models.Model):

    _name = "ai.tool"
    _description = "AI Tool"

    name = fields.Char(required=True)
    description = fields.Text()
    implementation = fields.Selection(
        [
            ("code", "Python Code"),
            ("method", "Python Method"),
        ],
        required=True,
        default="code",
    )
    model_id = fields.Many2one("ir.model", readonly=True, ondelete="cascade")
    function_name = fields.Char(readonly=True)
    kind = fields.Selection(
        [
            ("generic", "Generic"),
            ("generic_model", "Generic but requires a record to work"),
            ("record", "Record"),
        ],
        required=True,
        default="generic",
    )
    input_schema = fields.Text(
        default="{}",
        help="JSON Schema properties accepted by this tool.",
    )
    required_inputs = fields.Char(
        help="Comma-separated input names required by this tool.",
    )
    output_schema = fields.Text(
        default="{}",
        help="JSON Schema properties returned by this tool.",
    )
    code = fields.Text(
        default="""# Available variables:
# env, tool, record, args, date, datetime
# Assign a dict to result.
result = {}
""",
        help="Python code used when the implementation is Python Code.",
    )

    def _get_required_inputs(self):
        self.ensure_one()
        return [
            input_name.strip()
            for input_name in (self.required_inputs or "").split(",")
            if input_name.strip()
        ]

    def _get_json_schema_properties(self, field_name):
        self.ensure_one()
        raw_value = getattr(self, field_name) or "{}"
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ValidationError(
                f"{self._fields[field_name].string} must be valid JSON: {error}"
            ) from error
        if not isinstance(value, dict):
            raise ValidationError(
                f"{self._fields[field_name].string} must be a JSON object"
            )
        return value

    def _get_code_tool_schema(self, field_name):
        schema = self._get_json_schema_properties(field_name)
        if schema.get("type") == "object":
            return schema
        return {
            "type": "object",
            "properties": schema,
        }

    def _get_code_tool_definition(self):
        input_schema = self._get_code_tool_schema("input_schema")
        input_schema["required"] = self._get_required_inputs()
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": input_schema,
            "outputSchema": self._get_code_tool_schema("output_schema"),
        }

    def _get_tool_definition(self):
        self.ensure_one()
        if self.implementation == "code":
            return self._get_code_tool_definition()
        func = getattr(self.env[self.model_id.model], self.function_name)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": func._ai_tool["input_schema"],
            "outputSchema": func._ai_tool["output_schema"],
        }

    @aitool(
        input_schema={},
        output_schema={
            "date": {"type": "date"},
        },
    )
    def _ai_get_date(self):
        return {"date": date.today().isoformat()}

    @aitool(
        input_schema={
            "message": {"type": "string"},
        },
        required_inputs=["message"],
        output_schema={},
    )
    def _ai_post_message(self, message=None, record=None, **kwargs):
        if not record or not record.exists():
            raise ValueError("Record must be provided and exist to post a message")
        record.message_post(body=self._ai_post_message_parse_body(message))
        return {}

    def _ai_post_message_parse_body(self, message):
        """
        Using markdown library if available to convert markdown to html,
        otherwise using plaintext2html as fallback
        """
        if markdown:
            return html_sanitize(markdown.markdown(message))
        return plaintext2html(message)

    def _execute_code_tool(self, record=None, **kwargs):
        self.ensure_one()
        eval_context = {
            "env": self.env,
            "tool": self,
            "record": record,
            "args": kwargs,
            "date": date,
            "datetime": datetime,
            "result": {},
        }
        safe_eval(self.code or "", eval_context, mode="exec", nocopy=True)
        result = eval_context.get("result") or {}
        if not isinstance(result, dict):
            raise ValueError("Tool code must assign a dict to result")
        return result

    def _execute_method_tool(self, *args, record=None, **kwargs):
        self.ensure_one()
        method = getattr(self.env[self.model_id.model], self.function_name)
        if record:
            return method(*args, record=record, **kwargs)
        return method(*args, **kwargs)

    def _execute_tool_implementation(self, *args, record=None, **kwargs):
        if self.implementation == "code":
            return self._execute_code_tool(record=record, **kwargs)
        return self._execute_method_tool(*args, record=record, **kwargs)

    def _execute_tool(self, *args, record=None, **kwargs):
        self.ensure_one()
        if self.kind == "generic":
            return self._execute_tool_implementation(*args, **kwargs)
        if not record:
            raise ValueError("Record must be provided for non-generic tools")
        if self.kind == "generic_model":
            return self._execute_tool_implementation(*args, record=record, **kwargs)
        elif record._name != self.model_id.model:
            raise ValueError(
                f"Record model {record._name} does not match tool model "
                f"{self.model_id.model}"
            )
        if self.implementation == "code":
            return self._execute_code_tool(record=record, **kwargs)
        return getattr(record, self.function_name)(*args, **kwargs) or {}

    def _check_tool_configuration(self):
        for tool in self:
            if tool.implementation == "method" and (
                not tool.model_id or not tool.function_name
            ):
                raise ValidationError(
                    "Python Method tools must define a model and function name."
                )
            if tool.implementation == "code" and not (tool.code or "").strip():
                raise ValidationError("Python Code tools must define code.")
            if (
                tool.implementation == "code"
                and tool.kind == "record"
                and not tool.model_id
            ):
                raise ValidationError("Record tools must define a model.")
            tool._get_json_schema_properties("input_schema")
            tool._get_json_schema_properties("output_schema")

    @api.constrains(
        "implementation",
        "model_id",
        "function_name",
        "kind",
        "input_schema",
        "output_schema",
        "code",
    )
    def _check_ai_tool_configuration(self):
        self._check_tool_configuration()
