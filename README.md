# Readme for Copy-Fuse

## Usage Prequisites

<table>
    <thead>
	<tr>
	    <td>General Name</td>
	    <td>ArchLinux/CopyBoxLinux Package</td>
	</tr>
    </thead>
    <tbody>
	<tr>
	    <td>Python 2</td>
	    <td>python2</td>
	</tr>
	<tr>
	    <td>Fuse</td>
	    <td>fuse</td>
	</tr>
	<tr>
	    <td>Python Fuse Bindings</td>
	    <td>python2-fuse</td>
	</tr>
	<tr>
	    <td>Python URLLib3</td>
	    <td>python2-urllib3</td>
	</tr>
	<tr>
	    <td>Python Setuptools utility</td>
	    <td>python2-distribute</td>
	</tr>
    </tbody>
</table>

## Usage How To

1. Clone the copy-fuse repo
2. 'cd' to the copy-fuse repo
3. ensure that copyfuse.py is executable
4. execute copyfuse.py

## Troubleshooting

- SyntaxError: invalid token on line 228

  > The mount point given as the third argument to copyfuse.py does not exist, or is not a directory
