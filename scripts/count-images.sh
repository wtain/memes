base_path="/c/Users/ramiz/OneDrive/Pictures/Samsung Gallery/DCIM"

for path in "$base_path"/*/;
do
  [ -d "$path" ] || break
  # file="${path##*/}"
  file=$(basename "$path")

  count=$(find "$path" -maxdepth 1 -type f -printf '.' | wc -c)
  echo "$file: $count"
done
