from setuptools import find_packages, setup

package_name = "jcheng_hardware"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="JerichoCheng",
    maintainer_email="chengkewei77@gmail.com",
    description="Mock hardware bridge diagnostics",
    license="Proprietary (license review pending)",
    tests_require=["pytest"],
    entry_points={"console_scripts": ["jcheng_hardware_check = jcheng_hardware.node:main"]},
)
