import config

def chunk_document(document, chunk_size):
    """Split a document string into chunks of approximately chunk_size tokens.

    Tries to break on paragraph boundaries first, then newlines, then spaces.
    Falls back to a hard split if no clean break point is found.
    """
    chunked_document = []
    current_position = 0

    while current_position < len(document):
        split_point = current_position + (chunk_size * config.CHARS_PER_TOKEN)

        if split_point < len(document):
            # Try progressively less ideal break points
            clean_split_point = document.rfind("\n\n", current_position, split_point)
            if clean_split_point == -1:
                clean_split_point = document.rfind("\n", current_position, split_point)
            if clean_split_point == -1:
                clean_split_point = document.rfind(" ", current_position, split_point)
            if clean_split_point == -1:
                clean_split_point = split_point

            chunk = document[current_position:clean_split_point]
            current_position = clean_split_point + 1
        else:
            # Remaining text fits in one chunk
            chunk = document[current_position:]
            current_position = len(document)

        chunked_document.append(chunk)

    return chunked_document