// Backported from decode-uri-component 0.5.0 (MIT), GHSA-vcc3-ghjq-m6fr.
// Source: https://github.com/SamVerschueren/decode-uri-component/releases/tag/v0.5.0
// Keep the installed 0.2.2 CommonJS export and plus-to-space behavior.
// Local allocation change: collect output chunks, then join once (Hermes-safe).
const hexPair = /^[a-f\d]{2}$/i;

// Read a `%XX` sequence at `position`, returning the byte value and where to continue scanning.
function parsePercentByte(input, position) {
	if (input.codePointAt(position) !== 37 || position + 3 > input.length) {
		return;
	}

	const digits = input.slice(position + 1, position + 3);

	if (!hexPair.test(digits)) {
		return;
	}

	return {byte: Number.parseInt(digits, 16), next: position + 3};
}

/**
 * Return how many bytes a UTF-8 code point needs based on its lead byte.
 * Returns 0 for continuation bytes and other invalid lead bytes.
 */
function utf8SequenceLength(byte) {
	if (byte <= 0x7F) {
		return 1;
	}

	// Start at 0xC2 to exclude overlong 2-byte encodings (0xC0/0xC1).
	if (byte >= 0xC2 && byte <= 0xDF) {
		return 2;
	}

	if (byte >= 0xE0 && byte <= 0xEF) {
		return 3;
	}

	if (byte >= 0xF0 && byte <= 0xF4) {
		return 4;
	}

	return 0;
}

function isContinuationByte(byte) {
	return byte >= 0x80 && byte <= 0xBF;
}

/**
 * Decode as much of `input` as possible without throwing.
 *Scans left-to-right in O(n), decoding valid UTF-8 runs and leaving the rest literal.
 */
function decode(input) {
	try {
		return decodeURIComponent(input);
	} catch {
		const output = [];
		let position = 0;

		while (position < input.length) {
			if (input.codePointAt(position) !== 37) {
				output.push(input.charAt(position));
				position++;
				continue;
			}

			const firstByte = parsePercentByte(input, position);

			// `%` not followed by two hex digits (e.g. `%` or `%G`).
			if (!firstByte) {
				output.push(input.charAt(position));
				position++;
				continue;
			}

			const sequenceLength = utf8SequenceLength(firstByte.byte);

			// Continuation byte or invalid lead byte — emit one `%XX` literally.
			if (sequenceLength === 0) {
				output.push(input.slice(position, position + 3));
				position += 3;
				continue;
			}

			let end = firstByte.next;
			let validSequence = true;

			for (let index = 1; index < sequenceLength; index++) {
				const nextByte = parsePercentByte(input, end);

				if (!nextByte || !isContinuationByte(nextByte.byte)) {
					validSequence = false;
					break;
				}

				end = nextByte.next;
			}

			if (validSequence) {
				const encodedSequence = input.slice(position, end);

				try {
					output.push(decodeURIComponent(encodedSequence));
					position = end;
					continue;
				} catch {
					// Invalid UTF-8 despite correct structure — emit the first byte literally.
				}
			}

			// Missing continuation bytes or decode failure — emit the first byte literally.
			output.push(input.slice(position, position + 3));
			position += 3;
		}

		return output.join('');
	}
}

// Local wrapper: fixed regular expressions and one callback pass avoid the
// legacy map's input-sized RegExp compilation and repeated whole-input scans.
// Keep its fallback BOM and orphan-C2 handling; well-formed URI input still
// takes the unchanged built-in fast path below.
function customDecodeURIComponent(input) {
	return input
		.replace(/%FE%FF|%FF%FE/g, '\uFFFD\uFFFD')
		.replace(multiMatcher, match => decode(match))
		.replace(/%C2/g, '\uFFFD');
}
