"""MCP Text Editor Server implementation."""

import json
import logging
import os
import traceback
from collections.abc import Sequence
from typing import Any, Dict, List

from mcp.server import Server
from mcp.types import TextContent, Tool

from .text_editor import TextEditor
from .version import __version__

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-text-editor")

app = Server("mcp-text-editor")


class GetTextFileContentsHandler:
    """Handler for getting text file contents."""

    name = "get_text_file_contents"
    description = "Read text file contents from multiple files and line ranges. Returns file contents with hashes for concurrency control and line numbers for reference. The hashes are used to detect conflicts when editing the files. File paths must be absolute."

    def __init__(self):
        self.editor = TextEditor()

    def get_tool_description(self) -> Tool:
        """Get the tool description."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "description": "List of files and their line ranges to read",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "Path to the text file. File path must be absolute.",
                                },
                                "ranges": {
                                    "type": "array",
                                    "description": "List of line ranges to read from the file",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start": {
                                                "type": "integer",
                                                "description": "Starting line number (1-based)",
                                            },
                                            "end": {
                                                "type": ["integer", "null"],
                                                "description": "Ending line number (null for end of file)",
                                            },
                                        },
                                        "required": ["start"],
                                    },
                                },
                            },
                            "required": ["file_path", "ranges"],
                        },
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: 'utf-8')",
                        "default": "utf-8",
                    },
                },
                "required": ["files"],
            },
        )

    async def run_tool(self, arguments: Dict[str, Any]) -> Sequence[TextContent]:
        """Execute the tool with given arguments."""
        try:
            if "files" not in arguments:
                raise RuntimeError("Missing required argument: 'files'")

            for file_info in arguments["files"]:
                if not os.path.isabs(file_info["file_path"]):
                    raise RuntimeError(
                        f"File path must be absolute: {file_info['file_path']}"
                    )

            encoding = arguments.get("encoding", "utf-8")
            result = await self.editor.read_multiple_ranges(
                arguments["files"], encoding=encoding
            )
            response = result

            return [TextContent(type="text", text=json.dumps(response, indent=2))]

        except KeyError as e:
            raise RuntimeError(f"Missing required argument: '{e}'") from e
        except Exception as e:
            raise RuntimeError(f"Error processing request: {str(e)}") from e


class EditTextFileContentsHandler:
    """Handler for editing text file contents."""

    name = "edit_text_file_contents"
    description = "A line editor that supports editing text file contents by specifying line ranges and content. It handles multiple patches in a single operation with hash-based conflict detection. File paths must be absolute. IMPORTANT: (1) Before using this tool, you must first get the file's current hash and range hashes and line numbers using get_text_file_contents. (2) To avoid line number shifts affecting your patches, use get_text_file_contents to read the SAME ranges you plan to edit before making changes. different line numbers have different rangehashes.(3) Patches must be specified from bottom to top to handle line number shifts correctly, as edits to lower lines don't affect the line numbers of higher lines. (4) To append content to a file, first get the total number of lines with get_text_file_contents, then specify a patch with start = total_lines + 1 and end = total_lines. This indicates an append operation and range_hash is not required. Similarly, range_hash is not required for new file creation."

    def __init__(self):
        self.editor = TextEditor()

    def get_tool_description(self) -> Tool:
        """Get the tool description."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the text file. File path must be absolute.",
                                },
                                "file_hash": {
                                    "type": "string",
                                    "description": "Hash of the file contents when get_text_file_contents is called.",
                                },
                                "patches": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "start": {
                                                "type": "integer",
                                                "default": 1,
                                                "description": "Starting line number (1-based). it should be matched with the start line number when get_text_file_contents is called.",
                                            },
                                            "end": {
                                                "type": ["integer", "null"],
                                                "default": None,
                                                "description": "Ending line number (null for end of file). it should be matched with the end line number when get_text_file_contents is called.",
                                            },
                                            "contents": {"type": "string"},
                                            "range_hash": {
                                                "type": "string",
                                                "description": "Hash of the content being replaced from start to end (required except for new files and append operations)",
                                            },
                                        },
                                        "required": ["contents"],
                                    },
                                },
                            },
                            "required": ["path", "file_hash", "patches"],
                        },
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: 'utf-8')",
                        "default": "utf-8",
                    },
                },
                "required": ["files"],
            },
        )

    async def run_tool(self, arguments: Dict[str, Any]) -> Sequence[TextContent]:
        """Execute the tool with given arguments."""
        try:
            if "files" not in arguments:
                raise RuntimeError("Missing required argument: files")

            files = arguments["files"]
            results: Dict[str, Dict] = {}

            if len(files) == 0:
                return [TextContent(type="text", text=json.dumps(results, indent=2))]

            for file_operation in files:
                # First check if required fields exist
                if "path" not in file_operation:
                    raise RuntimeError("Missing required field: path")
                if "file_hash" not in file_operation:
                    raise RuntimeError("Missing required field: file_hash")
                if "patches" not in file_operation:
                    raise RuntimeError("Missing required field: patches")

                # Then check if path is absolute
                if not os.path.isabs(file_operation["path"]):
                    raise RuntimeError(
                        f"File path must be absolute: {file_operation['path']}"
                    )

                try:
                    file_path = file_operation["path"]
                    file_hash = file_operation["file_hash"]
                    patches = file_operation["patches"]

                    if not patches:
                        results[file_path] = {
                            "result": "error",
                            "reason": "Empty patches list",
                            "file_hash": file_hash,
                        }
                        continue

                    encoding = arguments.get("encoding", "utf-8")
                    result = await self.editor.edit_file_contents(
                        file_path, file_hash, patches, encoding=encoding
                    )
                    results[file_path] = result
                except Exception as e:
                    current_hash = None
                    if "path" in file_operation:
                        file_path = file_operation["path"]
                        try:
                            _, _, _, current_hash, _, _ = (
                                await self.editor.read_file_contents(file_path)
                            )
                        except Exception:
                            current_hash = None

                    results[file_path if "path" in file_operation else "unknown"] = {
                        "result": "error",
                        "reason": str(e),
                        "file_hash": current_hash,
                    }

            return [TextContent(type="text", text=json.dumps(results, indent=2))]
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            logger.error(traceback.format_exc())
            raise RuntimeError(f"Error processing request: {str(e)}") from e


class CreateTextFileHandler:
    """Handler for creating a new text file."""

    name = "create_text_file"
    description = (
        "Create a new text file with given content. The file must not exist already."
    )

    def __init__(self):
        self.editor = TextEditor()

    def get_tool_description(self) -> Tool:
        """Get the tool description."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the text file. File path must be absolute.",
                    },
                    "contents": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: 'utf-8')",
                        "default": "utf-8",
                    },
                },
                "required": ["path", "contents"],
            },
        )

    async def run_tool(self, arguments: Dict[str, Any]) -> Sequence[TextContent]:
        """Execute the tool with given arguments."""
        try:
            if "path" not in arguments:
                raise RuntimeError("Missing required argument: path")
            if "contents" not in arguments:
                raise RuntimeError("Missing required argument: contents")

            file_path = arguments["path"]
            if not os.path.isabs(file_path):
                raise RuntimeError(f"File path must be absolute: {file_path}")

            # Check if file already exists
            if os.path.exists(file_path):
                raise RuntimeError(f"File already exists: {file_path}")

            encoding = arguments.get("encoding", "utf-8")

            # Create new file using edit_file_contents with empty expected_hash
            result = await self.editor.edit_file_contents(
                file_path,
                expected_hash="",  # Empty hash for new file
                patches=[
                    {
                        "start": 1,
                        "end": None,
                        "contents": arguments["contents"],
                        "range_hash": "",  # Empty range_hash for new file
                    }
                ],
                encoding=encoding,
            )
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            logger.error(traceback.format_exc())
            raise RuntimeError(f"Error processing request: {str(e)}") from e


class AppendTextFileContentsHandler:
    """Handler for appending content to an existing text file."""

    name = "append_text_file_contents"
    description = "Append content to an existing text file. The file must exist."

    def __init__(self):
        self.editor = TextEditor()

    def get_tool_description(self) -> Tool:
        """Get the tool description."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the text file. File path must be absolute.",
                    },
                    "contents": {
                        "type": "string",
                        "description": "Content to append to the file",
                    },
                    "file_hash": {
                        "type": "string",
                        "description": "Hash of the file contents for concurrency control",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: 'utf-8')",
                        "default": "utf-8",
                    },
                },
                "required": ["path", "contents", "file_hash"],
            },
        )

    async def run_tool(self, arguments: Dict[str, Any]) -> Sequence[TextContent]:
        """Execute the tool with given arguments."""
        try:
            if "path" not in arguments:
                raise RuntimeError("Missing required argument: path")
            if "contents" not in arguments:
                raise RuntimeError("Missing required argument: contents")
            if "file_hash" not in arguments:
                raise RuntimeError("Missing required argument: file_hash")

            file_path = arguments["path"]
            if not os.path.isabs(file_path):
                raise RuntimeError(f"File path must be absolute: {file_path}")

            # Check if file exists
            if not os.path.exists(file_path):
                raise RuntimeError(f"File does not exist: {file_path}")

            encoding = arguments.get("encoding", "utf-8")

            # Check file contents and hash before modification
            # Get file information and verify hash
            content, _, _, current_hash, total_lines, _ = (
                await self.editor.read_file_contents(file_path, encoding=encoding)
            )

            # Verify file hash
            if current_hash != arguments["file_hash"]:
                raise RuntimeError("File hash mismatch - file may have been modified")

            # Ensure the append content ends with newline
            append_content = arguments["contents"]
            if not append_content.endswith("\n"):
                append_content += "\n"

            # Create patch for append operation
            result = await self.editor.edit_file_contents(
                file_path,
                expected_hash=arguments["file_hash"],
                patches=[
                    {
                        "start": total_lines + 1,
                        "end": None,
                        "contents": append_content,
                        "range_hash": "",
                    }
                ],
                encoding=encoding,
            )

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            logger.error(traceback.format_exc())
            raise RuntimeError(f"Error processing request: {str(e)}") from e


class DeleteTextFileContentsHandler:
    """Handler for deleting content from a text file."""

    name = "delete_text_file_contents"
    description = "Delete specified content ranges from a text file. The file must exist. File paths must be absolute."

    def __init__(self):
        self.editor = TextEditor()

    def get_tool_description(self) -> Tool:
        """Get the tool description."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the text file. File path must be absolute.",
                    },
                    "file_hash": {
                        "type": "string",
                        "description": "Hash of the file contents for concurrency control",
                    },
                    "ranges": {
                        "type": "array",
                        "description": "List of line ranges to delete",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": {
                                    "type": "integer",
                                    "description": "Starting line number (1-based)",
                                },
                                "end": {
                                    "type": ["integer", "null"],
                                    "description": "Ending line number (null for end of file)",
                                },
                                "range_hash": {
                                    "type": "string",
                                    "description": "Hash of the content being deleted",
                                },
                            },
                            "required": ["start", "range_hash"],
                        },
                    },
                    "encoding": {
                        "type": "string",
                        "description": "Text encoding (default: 'utf-8')",
                        "default": "utf-8",
                    },
                },
                "required": ["file_path", "file_hash", "ranges"],
            },
        )

    async def run_tool(self, arguments: Dict[str, Any]) -> Sequence[TextContent]:
        """Execute the tool with given arguments."""
        try:
            # Input validation
            if "file_path" not in arguments:
                raise RuntimeError("Missing required argument: file_path")
            if "file_hash" not in arguments:
                raise RuntimeError("Missing required argument: file_hash")
            if "ranges" not in arguments:
                raise RuntimeError("Missing required argument: ranges")

            file_path = arguments["file_path"]
            if not os.path.isabs(file_path):
                raise RuntimeError(f"File path must be absolute: {file_path}")

            # Check if file exists
            if not os.path.exists(file_path):
                raise RuntimeError(f"File does not exist: {file_path}")

            encoding = arguments.get("encoding", "utf-8")

            # Create patches for deletion (replacing content with empty string)
            patches = [
                {
                    "start": r["start"],
                    "end": r["end"],
                    "contents": "",
                    "range_hash": r["range_hash"],
                }
                for r in arguments["ranges"]
            ]

            # Use the existing edit_file_contents method
            result = await self.editor.edit_file_contents(
                file_path,
                expected_hash=arguments["file_hash"],
                patches=patches,
                encoding=encoding,
            )

            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            logger.error(traceback.format_exc())
            raise RuntimeError(f"Error processing request: {str(e)}") from e


# Initialize tool handlers
get_contents_handler = GetTextFileContentsHandler()
edit_contents_handler = EditTextFileContentsHandler()
create_file_handler = CreateTextFileHandler()
append_file_handler = AppendTextFileContentsHandler()
delete_contents_handler = DeleteTextFileContentsHandler()


@app.list_tools()
async def list_tools() -> List[Tool]:
    """List available tools."""
    return [
        get_contents_handler.get_tool_description(),
        edit_contents_handler.get_tool_description(),
        create_file_handler.get_tool_description(),
        append_file_handler.get_tool_description(),
        delete_contents_handler.get_tool_description(),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    """Handle tool calls."""
    logger.info(f"Calling tool: {name}")
    try:
        if name == get_contents_handler.name:
            return await get_contents_handler.run_tool(arguments)
        elif name == edit_contents_handler.name:
            return await edit_contents_handler.run_tool(arguments)
        elif name == create_file_handler.name:
            return await create_file_handler.run_tool(arguments)
        elif name == append_file_handler.name:
            return await append_file_handler.run_tool(arguments)
        elif name == delete_contents_handler.name:
            return await delete_contents_handler.run_tool(arguments)
        else:
            raise ValueError(f"Unknown tool: {name}")
    except ValueError:
        logger.error(traceback.format_exc())
        raise
    except Exception as e:
        logger.error(traceback.format_exc())
        raise RuntimeError(f"Error executing command: {str(e)}") from e


async def main() -> None:
    """Main entry point for the MCP text editor server."""
    logger.info(f"Starting MCP text editor server v{__version__}")
    try:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        raise
