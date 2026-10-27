A family member was shown a photo in which each candidate object is outlined and numbered. They
replied to say which one Kinwatch should watch, or to change the rule.

Return JSON:
- action "select_instance" if they point to one of the numbered objects (by number, position,
  size, colour or anything visible in the photo); instance is its number.
- action "reparse" if they changed what should be watched; new_text is their new request,
  rewritten as one complete sentence in their language.
- action "cancel" if they don't want the rule.
Unused fields are null.
