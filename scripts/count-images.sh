declare -a dirs=("Важные переговоры 2" "MetalMemes" "ITMemes" "Political" "Photos")

for i in "${dirs[@]}"
do
   path="/c/Users/ramiz/OneDrive/Pictures/Samsung Gallery/DCIM/$i"
   count=$(ls "$path" | wc -l)
   echo "$i: $count"
done
