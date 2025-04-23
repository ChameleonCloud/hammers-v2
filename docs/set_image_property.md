# set_image_property.py

The set_image_property script allows an operator to set
a metadata field on a Glance image for a single image or
multiple images all at once.

The script requires a site.yaml file configured with the a
single setting:
```
---
image_store_cloud: uc_dev
```

The name of the image_store_cloud should match an entry in
your OpenStack clouds.yaml file and be the name of the site
with the images you wish to set metadata on.

The tool can either be used to set a field for a single image,
such as:
```
$ set_image_property --site-yaml config/site.yaml --metadata-field chameleon-supported --single-value 8ce4cdba-5d9d-4cd5-b0c2-65795e64d720:true
```

In this example the tool would set the metadata field `chameleon-supported` to a value of `true` on
image `8ce4cdba-5d9d-4cd5-b0c2-65795e64d720`.

If you wish to bulk update multiple images, you can pass a text
file to the tool with the option `--values-file`:
```
$ set_image_property --site-yaml config/site.yaml --metadata-field chameleon-supported --values-file image_values.txt
```

The text file should be in the format `uuid:value`, such as:
```
# this is a comment
e3b73771-cb97-45f9-8278-58c1ecccabb7:true
8ce4cdba-5d9d-4cd5-b0c2-65795e64d720:deprecated
b3a6c5a7-1bfb-4e62-bd01-4f6c90bbf327:false
```
