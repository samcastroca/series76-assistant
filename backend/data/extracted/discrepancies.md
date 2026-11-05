# Extraction discrepancies

Cells where the two Gemini runs disagree. Check each one against the PDF and add
an entry to `data/corrections.yaml`; `build` refuses to run until every cell here
is covered.

| table | row | part number (run 1) | field | run 1 | run 2 |
|---|---|---|---|---|---|
| xp_220v_1ph |  |  | motor_power_groups | [{'watts': '15', 'first_part_number': '761X30-11320000/A', 'last_part_number': '761Y30-11320000/A'}, {'watts': '40', 'first_part_number': '762X30-11320000/A', 'last_part_number': '763X30-11320000/A'}, {'watts': '90', 'first_part_number': '763Y30-11320000/A', 'last_part_number': '763Y30-11320000/A'}, {'watts': '180', 'first_part_number': '764X30-11320000/A', 'last_part_number': '765Y30-11320000/A'}] | [{'watts': '15', 'first_part_number': '761X30-11320000/A', 'last_part_number': '761Y30-11320000/A'}, {'watts': '40', 'first_part_number': '762X30-11320000/A', 'last_part_number': '763X30-11320000/A'}, {'watts': '90', 'first_part_number': '763Y30-11320000/A', 'last_part_number': '764X30-11320000/A'}, {'watts': '180', 'first_part_number': '764Y30-11320000/A', 'last_part_number': '765Y30-11320000/A'}] |
